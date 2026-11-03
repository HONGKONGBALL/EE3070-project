"""Single camera, shared live preview, ground vision and distance-gated object speech."""

import argparse
from collections import deque
import json
import os
from pathlib import Path
import signal
import threading
import time
import tomllib
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

from .camera import ReplayCamera
from .demo import Demo, ROOT, handler_for
from .detector import Detector
from .ground import RoadDetector, SemanticDetector
from .yolox import YoloXPotholeDetector, NcnnYoloXPotholeDetector
from .puddle import PuddleDetector
from .quality import assess_frame
from .stairs import StairDirectionDetector, confirm_stairs
from .distance import NoDistanceProvider, MockDistanceProvider, PicoDistanceProvider
from .speech import Speech
from .audio_outputs import AudioOutputControl
from .crowd import CrowdAwareness
from .voice import VOICE
from .v2_policy import V2Policy, task_hazards, support_potholes
from .diagnostics import ground_diagnostic


def load_config(path, *, mode=None, models_root=None):
    """Use one config on the Pi or with the same weights stored elsewhere locally."""
    with Path(path).open("rb") as file:
        config = tomllib.load(file)
    config['_config_path'] = str(Path(path).resolve())
    if mode is not None:
        config.setdefault("vision", {})["mode"] = mode
    if models_root is not None:
        for name in ("model", "road", "pothole", "semantic", "puddle", "stairs"):
            task = config.get(name, {})
            if task.get("directory"):
                relative = Path(task["directory"]).relative_to("models")
                task["directory"] = str(Path(models_root).resolve()/relative)
        semantic = config.get("semantic", {})
        if semantic.get("stair_context_directory"):
            relative = Path(semantic["stair_context_directory"]).relative_to("models")
            semantic["stair_context_directory"] = str(Path(models_root).resolve()/relative)
    return config


class V2(Demo):
    def __init__(self, config):
        pothole_backend = config.get('pothole', {}).get('backend', 'ncnn')
        if pothole_backend not in {'ncnn', 'yolox_onnx_research', 'yolox_ncnn_research'}:
            raise ValueError('Unknown explicit pothole backend')
        source = config.get("source", {}).get("video", "")
        if source and config["distance"]["mode"] == "pico":
            raise ValueError("Recorded video cannot be associated with a live Pico distance")
        if config.get("simulation", {}).get("timeline") and not source:
            raise ValueError("Simulation timeline requires an explicit replay source")
        self.vision_mode = config.get("vision", {}).get("mode", "balanced")
        if self.vision_mode not in {"balanced", "ground", "awareness"}:
            raise ValueError("Vision mode must be balanced, ground or awareness")
        self.awareness_mode = self.vision_mode == 'awareness'
        self.objects_enabled = self.vision_mode == "balanced"
        camera_factory = (lambda _device,w,h,fps: ReplayCamera(source,w,h,fps)) if source else None
        super().__init__(config, audio_factory=lambda *args: Speech(*args,
                         ordinary_interval_s=config["audio"].get("ordinary_interval_s",1.5),
                         allowed_phrases={v[0] for v in VOICE.values()} if self.awareness_mode else None), camera_factory=camera_factory)
        self.config, self.input_source = config, "replay" if source else "live_camera"
        self.audio_outputs = AudioOutputControl(self.audio, config.get('_config_path'))
        self.crowd = (CrowdAwareness(self.run_id,config.get('crowd'),self.log)
                      if self.awareness_mode and config.get('crowd',{}).get('enabled',True) else None)
        self.person_priority_applied = False
        distance = config["distance"]
        mode = distance["mode"]
        if mode == "mock":
            segments = json.loads((ROOT / distance["script"]).read_text())["segments"]
            self.distance = MockDistanceProvider(segments)
        elif mode == "pico":
            self.distance = PicoDistanceProvider(distance["port"], distance.get("baud",115200),
                                                 distance.get("direction","unknown"), distance.get("calibration_id",""))
        elif mode == "none":
            self.distance = NoDistanceProvider()
        else:
            raise ValueError("Distance mode must be none, mock, or pico")
        self.policy = V2Policy(self.run_id, config["warning"]["roi"], config.get("association",{}),
                               distance["enter_m"],distance["leave_m"], config["warning"]["ground_roi"], config["warning"], self.log)
        self.audio.validator = self._request_valid
        self.audio.on_delivery = self._on_delivery
        self.results = {}
        self.ground_tasks={"road":RoadDetector,"pothole":RoadDetector,"semantic":SemanticDetector}
        if pothole_backend == 'yolox_onnx_research':
            self.ground_tasks['pothole'] = YoloXPotholeDetector
        elif pothole_backend == 'yolox_ncnn_research':
            self.ground_tasks['pothole'] = NcnnYoloXPotholeDetector
        if config.get("puddle",{}).get("enabled",False):self.ground_tasks["puddle"]=PuddleDetector
        if config.get("stairs",{}).get("enabled",False):self.ground_tasks["stairs"]=StairDirectionDetector
        self.task_states = {name:{"state":"loading","error":"","fps":0} for name in ("objects",*self.ground_tasks)}
        if not self.objects_enabled:
            self.model_state = "disabled"
            self.task_states["objects"] = {"state":"disabled","error":"","fps":0}
        if self.awareness_mode:
            self.task_states['persons'] = {'state':'loading' if self.crowd else 'disabled','error':'','fps':0}
        self.sample = None
        self.workers = [*([self.thread] if self.objects_enabled or self.crowd else []),*[
                        threading.Thread(target=self._ground_loop,args=(name,factory),daemon=True)
                        for name,factory in self.ground_tasks.items()],
                        threading.Thread(target=self._render_loop,daemon=True)]
        self.context_valid = False
        self.indoor_floor_context = False
        self.surface_observation = None
        self.surface_labels,self.pavement_detected = [],False
        self.task_faults = {}
        self.ground_diagnostics = {}
        self.timeline=[]
        self.scene=None
        self.visual_quality={'state':'starting','usable':False,'reasons':['no_frame']}
        self.quality_after_frame=0
        if config.get('simulation',{}).get('timeline'):
            self.timeline=json.loads((ROOT/config['simulation']['timeline']).read_text())['cases']

    def start(self):
        self.started_at = time.monotonic()
        self.log("start_v2", input_source=self.input_source, distance_source=self.distance.source,
                 device=self.camera.device, vision_mode=self.vision_mode, config=self.config)
        self.distance.start()
        self.camera.start()
        for worker in self.workers: worker.start()

    def close(self):
        self.stopped.set()
        self.camera.close()
        with self.condition: self.condition.notify_all()
        for worker in self.workers: worker.join(timeout=5)
        self.distance.close()
        self.audio.close()
        self.log("stop")
        self.logfile.close()

    def _run(self):
        if self.crowd:
            try:
                if os.name == 'nt':
                    import ctypes
                    self.person_priority_applied = bool(ctypes.windll.kernel32.SetThreadPriority(ctypes.c_void_p(-2),-1))
                else:
                    os.setpriority(os.PRIO_PROCESS,threading.get_native_id(),5)
                    self.person_priority_applied = True
            except OSError:
                self.person_priority_applied = False
            config = {**self.config['model'],'size':self.crowd.cfg['person_size'],
                      'confidence':self.crowd.cfg['confidence'],'threads':1,'max_fps':1/self.crowd.cfg['interval_s']}
            self._model_loop('persons',Detector,config)
        else:
            self._model_loop("objects",Detector,self.config["model"])

    def _person_ground_fresh(self, now):
        with self.condition:
            return all(name in self.results and
                       0 <= now-self.results[name]['captured_at'] <= self.crowd.cfg['ground_max_age_s']
                       for name in self.ground_tasks)

    def _ground_loop(self,name,factory):
        self._model_loop(name,factory,self.config[name])

    def _model_loop(self,name,factory,cfg):
        completions, latencies = deque(maxlen=60),deque(maxlen=1200)
        try:
            extra={'ground_roi':self.config['warning']['ground_roi']} if name=='puddle' else {}
            if name == 'persons':extra = {'classes':(0,), 'max_detections':80}
            if name == "semantic" and cfg.get("stair_context_directory"):
                extra = {"stair_context_directory": ROOT/cfg["stair_context_directory"],
                         "ground_roi": self.config["warning"]["ground_roi"]}
            detector = factory(ROOT/cfg["directory"],cfg["size"],cfg["confidence"],cfg["threads"],**extra)
            if name == "objects": self.detector = detector
            self.log("model_ready",task=name,model=detector.model_id,
                     revision=getattr(detector,"manifest",{}).get("revision"),
                     input_size=cfg["size"],confidence=cfg["confidence"],threads=cfg["threads"])
        except Exception as exc:
            with self.condition: self.task_states[name] = {"state":"error","error":str(exc),"fps":0}
            if name == "objects": self.model_state = "error"
            self.log("model_error",task=name,message=str(exc))
            return
        previous, last_error = 0, ""
        sampled_count, unsampled_frames, quality_skips, stale_results, deferred = 0,0,0,0,0
        while not self.stopped.is_set():
            if name == 'persons' and not self._person_ground_fresh(time.monotonic()):
                deferred += 1
                with self.condition:
                    self.task_states[name].update(state='deferred',defer_reason='ground_observations_not_fresh',deferred_checks=deferred)
                self.stopped.wait(.1)
                continue
            sample = self.camera.next_frame(previous)
            if sample is None: continue
            image,frame_id,captured = sample
            unsampled_frames += max(0,frame_id-previous-1)
            sampled_count += 1
            previous, started = frame_id,time.monotonic()
            try:
                quality=assess_frame(image)
                if quality['usable']:
                    output = detector.predict(image)
                    if name in {'objects','persons'}:
                        output = {"detections":output}
                        if name == 'persons':
                            output['detections'] = [d for d in output['detections'] if d['label']=='person']
                else:
                    output={'detections':[]}
                output['visual_quality']=quality
                finished = time.monotonic()
                # Slow results are missing evidence, not a failed model or a clear path.
                stale = finished-captured > 1.5
                stale_results += int(stale)
                quality_skips += int(not quality["usable"])
                output.setdefault("candidates_observed_at", finished)
                output.setdefault("verification_completed_at", finished)
                h,w = image.shape[:2]
                for d in output["detections"]+output.get('candidate_detections',[]):
                    d["bbox_normalized"] = [float(v)/(w if i%2 == 0 else h) for i,v in enumerate(d["bbox"])]
                    d.update(frame_id=frame_id,captured_at=captured,source_model=detector.model_id)
                output.update(run_id=self.run_id,frame_id=frame_id,captured_at=captured,model_id=detector.model_id,
                              model_revision=getattr(detector,"manifest",{}).get("revision"))
                if quality['usable']:
                    completions.append(finished)
                    latencies.append((finished-captured)*1000)
                fps = (len(completions)-1)/(completions[-1]-completions[0]) if len(completions)>1 else 0
                with self.condition:
                    self.results[name] = output
                    self.task_states[name] = {"state":"limited" if stale else "ready","error":"","stale_result":stale,"fps":round(fps,2) if quality['usable'] else 0,
                                             "inference_ms":round((finished-started)*1000,1) if quality['usable'] else None,
                                             "prediction_skipped":not quality['usable'],"visual_quality":quality,
                                             "sampled_frames":sampled_count,"unsampled_capture_frames":unsampled_frames,
                                             "quality_skips":quality_skips,"stale_results":stale_results,
                                             "latency_p95_ms":round(float(np.percentile(latencies,95)),1) if latencies else None,
                                             "model":detector.model_id,"frame_id":frame_id,"captured_at":captured}
                    if name == 'persons':
                        self.task_states[name].update(deferred_checks=deferred,priority_lowered=self.person_priority_applied)
                    if name == "objects":
                        self.model_state,self.inference_ms = "ready",self.task_states[name]["inference_ms"]
                last_error = ""
            except Exception as exc:
                with self.condition:
                    self.results.pop(name,None)
                    self.task_states[name] = {"state":"error","error":str(exc),"fps":0}
                    if name=="objects": self.model_state="error"
                if str(exc)!=last_error: self.log("inference_error",task=name,message=str(exc))
                last_error = str(exc)
                self.stopped.wait(0.5)
            self.stopped.wait(max(0,1/cfg["max_fps"]-(time.monotonic()-started)))

    def _render_loop(self):
        previous, fault = 0, ""
        while not self.stopped.is_set():
            frame = self.camera.next_frame(previous,timeout=0.1)
            now = time.monotonic()
            if self.input_source=='replay' and self.distance.source=='mock':
                elapsed=(frame[1]-1)/self.camera.media_fps if frame else None
                self.sample=self.distance.latest(now,elapsed=elapsed) if frame else None
            else:
                self.sample=self.distance.latest(now)
            if frame is None:
                # No newer frame within 100ms is normal at low capture rates. Only
                # camera errors or the existing freshness limit invalidate vision.
                if self.camera.status()["ready"]:
                    for event in self._decision_events(now): self._emit(event)
                    self.audio.tick()
                    continue
                self.policy.clear_visual()
                if self.crowd:self.crowd.invalidate(now)
                current=self.audio.arbiter.current
                if current and not self._request_valid(current):self.audio.sink.stop()
                self.visual_quality={'state':'unavailable','usable':False,'reasons':['no_fresh_frame']}
                self.context_valid,self.pavement_detected,self.surface_labels = False,False,[]
                self.indoor_floor_context = False
                self.surface_observation = None
                with self.condition: self.ground_diagnostics = {}
                message = self.camera.status()["error"] or "No fresh frame"
                if message == "Starting camera" and now-self.started_at < 3:
                    with self.condition: self.error = "Starting camera"
                    self.audio.tick()
                    continue
                if fault != message:
                    self.audio.clear_pending()
                    if message != "Replay finished":
                        self.audio.submit({"phrase_id":"camera_unavailable","ttl_ms":5000,"priority":"CRITICAL",
                                           "event_id":"fault:camera"})
                    self.log("camera_error" if message!="Replay finished" else "replay_finished",message=message)
                with self.condition: self.jpeg,self.error,self.detections = None,message,[]
                fault = message
                for event in self._decision_events(now): self._emit(event)
                self.audio.tick()
                continue
            image,frame_id,captured = frame
            previous, fault = frame_id,""
            with self.condition:
                snapshots = dict(self.results)
                faults = {k:v["error"] for k,v in self.task_states.items() if v["state"]=="error"}
            snapshots = self._scene_results(frame_id, snapshots,captured)
            snapshots = self._quality_results(image,frame_id,snapshots)
            for name,message in faults.items():
                if self.task_faults.get(name) != message:
                    self.audio.submit({"phrase_id":"object_unavailable" if name in {'objects','persons'} else "ground_unavailable",
                                       "priority":"SYSTEM","ttl_ms":5000,"event_id":"fault:"+name})
            self.task_faults = faults
            objects = snapshots.get("objects")
            if objects and now-objects["captured_at"]>0.5: objects = None
            semantic = snapshots.get("semantic")
            if semantic and now-semantic["captured_at"]>1.5: semantic = None
            roi = np.zeros(image.shape[:2],np.uint8)
            polygon = (np.asarray(self.config["warning"]["ground_roi"])*[image.shape[1],image.shape[0]]).astype(np.int32)
            cv2.fillPoly(roi,[polygon],1)
            self.context_valid = bool(semantic and np.count_nonzero(semantic["ground_mask"] & roi) >= np.count_nonzero(roi)*0.05)
            self.surface_labels = []
            self.pavement_detected = False
            self.surface_observation = None
            self.indoor_floor_context = False
            if semantic:
                valid = semantic["ground_mask"].astype(bool) & roi.astype(bool)
                paved=valid & np.isin(semantic['class_map'],[6,11,52])
                self.surface_labels = [int(v) for v in np.unique(semantic["class_map"][valid])]
                self.pavement_detected = bool(np.count_nonzero(paved) >= np.count_nonzero(roi)*0.1)
                self.indoor_floor_context = bool(np.count_nonzero(
                    np.isin(semantic['class_map'],[3,28]) & roi.astype(bool)) >= np.count_nonzero(roi)*.2)
                self.surface_observation={
                    'frame_id':semantic['frame_id'],'captured_at':semantic['captured_at'],
                    'observed_at':now,
                    'valid_until':semantic['captured_at']+1.5,'model_id':semantic['model_id'],
                    'model_revision':semantic.get('model_revision'),'context_valid':self.context_valid,
                    'pavement_detected':self.pavement_detected,'ground_class_ids':self.surface_labels,
                    'ground_fraction':round(float(np.count_nonzero(valid)/max(1,np.count_nonzero(roi))),4),
                    'pavement_fraction':round(float(np.count_nonzero(paved)/
                                                    max(1,np.count_nonzero(roi))),4),
                    'basis':'Semantic ground in image path ROI; not proof of safety or physical evenness'}
            self.policy.observe_objects(objects,now)
            if self.crowd:self.crowd.observe(snapshots.get('persons'),now,semantic)
            ground_inputs, blocked = {}, {}
            for name in self.ground_tasks:
                result = task_hazards(name,snapshots.get(name))
                if name=="semantic" and result is not None:
                    result={**result,"detections":[d for d in result["detections"] if d.get("context_supported",False)]}
                elif name=="stairs":
                    pending=result is not None and semantic is not None
                    result=confirm_stairs(result,semantic)
                    # Unpaired captures are not negative detections; normal policy freshness still expires old evidence.
                    if pending and result is None:
                        ground_inputs[name] = None
                        blocked[name] = "awaiting_semantic_pair"
                        continue
                elif name=="pothole":
                    result=support_potholes(result,semantic,self.context_valid,self.indoor_floor_context)
                    blocked[name] = "indoor_context_rejected" if self.indoor_floor_context else "strong_pothole_evidence_required"
                elif name!="puddle" and not self.context_valid:
                    result=None
                    blocked[name] = "ground_context_unavailable"
                ground_inputs[name] = result
                self.policy.observe_ground(name,result,now,
                    negative_reliable=(self.context_valid or self.indoor_floor_context)
                        if name=="semantic" else (name!="road" or self.context_valid))
            events = self._decision_events(now)
            for event in events: self._emit(event)
            diagnostics = {name:ground_diagnostic(name,snapshots.get(name),ground_inputs.get(name),
                self.policy,now,blocked_by=blocked.get(name,"")) for name in self.ground_tasks}
            self.audio.tick()
            detections = []
            if objects: detections.extend(objects["detections"])
            persons = snapshots.get('persons')
            if self.crowd and persons and now-persons['captured_at'] <= self.crowd.cfg['freshness_s']:
                detections.extend(persons['detections'])
            for name in self.ground_tasks:
                result = snapshots.get(name)
                if result and now-result["captured_at"]<=1.5:
                    detections.extend(task_hazards(name,result)["detections"])
            self._draw_v2(image,detections,semantic,now)
            ok,jpeg = cv2.imencode(".jpg",image,[cv2.IMWRITE_JPEG_QUALITY,80])
            if not ok: continue
            finished = time.monotonic()
            with self.condition:
                self.jpeg,self.error = jpeg.tobytes(),""
                self.sequence += 1
                self.frame_id,self.captured_at,self.detections = frame_id,captured,detections
                self.ground_diagnostics = diagnostics
                self.completed.append(finished)
                self.timings.append((finished-captured)*1000)
                self.condition.notify_all()
            self.stopped.wait(max(0,1/self.config["server"]["preview_fps"]-(time.monotonic()-now)))

    def _scene_results(self,frame_id,snapshots,captured_at=None):
        """A photo cut starts an independent case; previous tracks/audio/results expire."""
        case=next((c for c in self.timeline if c['start_frame']<=frame_id<=c['end_frame']),None)
        if case and (self.scene is None or case['id']!=self.scene['id']):
            self.scene=case
            self.scene_first_observed_at=captured_at
            self.audio.sink.stop()
            self.audio.arbiter.cancel_pending()
            self.policy=V2Policy(self.run_id+':'+case['id'],self.config['warning']['roi'],
                self.config.get('association',{}),self.config['distance']['enter_m'],self.config['distance']['leave_m'],
                self.config['warning']['ground_roi'], self.config['warning'], self.log)
            if getattr(self,'crowd',None):
                self.crowd = CrowdAwareness(self.run_id+':'+case['id'],self.config.get('crowd'),self.log)
            self.log('simulation_scene',case_id=case['id'],source_file=case['source_file'],frame_id=frame_id,
                     observed_frame_captured_at=captured_at)
        return {k:v for k,v in snapshots.items() if v['frame_id']>=case['start_frame']} if case else snapshots

    def _quality_results(self,image,frame_id,snapshots):
        quality=assess_frame(image)
        if quality['reasons']!=self.visual_quality.get('reasons'):
            self.log('visual_quality',frame_id=frame_id,**quality)
        self.visual_quality={**quality,'frame_id':frame_id}
        if not quality['usable']:
            self.quality_after_frame=frame_id
            self.policy.clear_visual()
            if getattr(self,'crowd',None):self.crowd.invalidate(time.monotonic())
            # A valid unassociated distance warning remains independent of image quality.
            self.audio.arbiter.cancel_pending(lambda req:not self._request_valid(req))
            current=self.audio.arbiter.current
            if current and not self._request_valid(current):self.audio.sink.stop()
            return {}
        return {name:result for name,result in snapshots.items()
                if result.get('visual_quality',{}).get('usable',True)
                and result['frame_id']>self.quality_after_frame}

    def _emit(self,event):
        event.update(input_source=self.input_source,simulation_case=self.scene['id'] if self.scene else None)
        self.log("warning",**event)
        with self.condition: self.events.append(event)
        self.audio.submit(event)

    def _decision_events(self, now):
        # Awareness never associates Pico distance with individual-object speech.
        events = self.policy.step(None if self.awareness_mode else self.sample,now)
        if self.crowd:
            ground_phrases = {VOICE[name][0] for name in ('STAIRS','DAMAGED_PAVEMENT','PUDDLE')}
            arbiter = self.audio.arbiter
            current_ground = arbiter.current and arbiter.current.prompt_id in ground_phrases
            if (any(e['kind']=='GROUND_HAZARD' for e in events) or current_ground or
                    any(p in ground_phrases for p in arbiter.pending()) or arbiter.safety_hold_active(now)):
                self.crowd.defer_speech(now)
            events.extend(self.crowd.step(now))
        return events

    def _on_delivery(self, event_id, action, now):
        if self.crowd and ':crowd-' in event_id:
            self.crowd.delivery(event_id,action,now)
        else:
            self.policy.episodes.delivery(event_id,action,now)

    def _request_valid(self,request):
        if getattr(self,'crowd',None) and ':crowd-' in request.event_id:
            return self.crowd.valid(request.event_id,time.monotonic())
        if request.event_id.startswith("fault:"):
            task = request.event_id.partition(":")[2]
            if task=="camera": return not self.camera.status()["ready"]
            return self.task_states.get(task,{}).get("state")=="error"
        return self.policy.request_valid(request,time.monotonic())

    def _draw_v2(self,image,detections,semantic,now):
        if semantic:
            classes = semantic["class_map"]
            tint = image.copy()
            tint[semantic["ground_mask"].astype(bool)] = (70,120,60)
            tint[np.isin(classes,[53,59,121])] = (40,120,210)
            cv2.addWeighted(tint,0.25,image,0.75,0,image)
        h,w = image.shape[:2]
        for d in detections:
            if d['label']=='person' and self.crowd and not self.crowd.cfg['show_person_boxes']:
                continue
            x1,y1,x2,y2 = (int(v) for v in d["bbox"])
            color = (70,220,80) if d.get("source_model")==Detector.model_id else (80,80,240)
            if d.get("localization") == "classification_roi":
                # Show the observed area without pretending to localize an object.
                color = (220,180,60)
                for x in range(x1,x2,16):
                    cv2.line(image,(x,y1),(min(x+8,x2),y1),color,1)
                    cv2.line(image,(x,y2),(min(x+8,x2),y2),color,1)
                for y in range(y1,y2,16):
                    cv2.line(image,(x1,y),(x1,min(y+8,y2)),color,1)
                    cv2.line(image,(x2,y),(x2,min(y+8,y2)),color,1)
                label = "Possible stairs: observed region"
            else:
                cv2.rectangle(image,(x1,y1),(x2,y2),color,2)
                label = f'{d["label"]} {d["confidence"]:.0%}'
            cv2.putText(image,label,(x1,max(18,y1-5)),cv2.FONT_HERSHEY_SIMPLEX,0.5,color,1)
        rois = [self.config["warning"]["ground_roi"]]
        if self.objects_enabled: rois.append(self.config["warning"]["roi"])
        for roi in rois:
            cv2.polylines(image,[(np.asarray(roi)*[w,h]).astype(np.int32)],True,(170,170,170),1)
        mode = "MOCK DISTANCE" if self.distance.source=="mock" else "DISTANCE "+self.distance.source.upper()
        cv2.rectangle(image,(0,0),(w,26),(15,15,15),-1)
        title=f'{self.vision_mode.upper()} | {self.input_source.upper()} | {mode}'
        if self.scene:title+=' | '+self.scene['id']
        cv2.putText(image,title,(8,18),cv2.FONT_HERSHEY_SIMPLEX,0.48,(250,250,250),1)
        if self.crowd:
            crowd=self.crowd.status(now)
            label=f"Crowd: {crowd['state']} | people seen {crowd['evidence'].get('person_count',0)}"
            cv2.rectangle(image,(0,26),(w,47),(15,15,15),-1)
            cv2.putText(image,label,(8,41),cv2.FONT_HERSHEY_SIMPLEX,.43,(250,250,250),1)
        active = [e for e in self.events if now-e['created_at']<=1.5 and
                  (e['event_id'] in self.policy.live_events or
                   (self.crowd and e['kind']=='CROWD' and self.crowd.valid(e['event_id'],now)))]
        self.current_text = ' / '.join(dict.fromkeys(e['text'] for e in active))
        cv2.rectangle(image,(0,h-(47 if self.crowd and len(active)>1 else 26)),(w,h),(15,15,15),-1)
        text = self.current_text or ("Visual limited: "+', '.join(self.visual_quality['reasons']) if not self.visual_quality['usable'] else
            "Ground context detected; no safety conclusion" if self.context_valid else "Ground context unavailable")
        if self.crowd and len(active)>1:
            ground=next((e['text'] for e in active if e['kind']=='GROUND_HAZARD'),active[0]['text'])
            notice=next((e['text'] for e in active if e['kind']=='CROWD'),active[-1]['text'])
            cv2.putText(image,ground,(8,h-28),cv2.FONT_HERSHEY_SIMPLEX,.43,(250,250,250),1)
            cv2.putText(image,notice,(8,h-8),cv2.FONT_HERSHEY_SIMPLEX,.43,(250,250,250),1)
        else:
            cv2.putText(image,text,(8,h-8),cv2.FONT_HERSHEY_SIMPLEX,0.46,(250,250,250),1)

    def status(self):
        status = super().status()
        status['audio'].update(enabled=self.audio.sink.enabled, target=self.audio.sink.target)
        now = time.monotonic()
        crowd = self.crowd.status(now) if self.crowd else {'enabled':False,'state':'DISABLED'}
        with self.condition:
            tasks = {k:{**v,"frame_age_ms":round((now-v["captured_at"])*1000) if v.get("captured_at") else None}
                     for k,v in self.task_states.items()}
            candidates=[d for result in self.results.values() if self.visual_quality['usable']
                        and result.get('visual_quality',{}).get('usable',True) and now-result['captured_at']<=1.5
                        and result['frame_id']>self.quality_after_frame
                        and (not self.scene or result['frame_id']>=self.scene['start_frame'])
                        for d in result.get('candidate_detections',[])]
            diagnostics = {name:entry if now-entry.get("captured_at",0)<=1.5 else
                {"state":"unavailable","reason":"no_fresh_result","candidates":[]}
                for name,entry in self.ground_diagnostics.items()}
        status.update(version=2,input_source=self.input_source,tasks=tasks,visual_quality=dict(self.visual_quality),
                      crowd=crowd,
                      vision_mode=self.vision_mode,ground_diagnostics=diagnostics,
                      hazard_episodes=self.policy.episodes.status(now),
                      episode_counters=dict(self.policy.episodes.counters),
                      audio_delivery=self.audio.delivery_status(),
                      ground_candidates=candidates,ground_context_valid=self.context_valid,
                      indoor_floor_context=self.indoor_floor_context,
                      simulation_case={'id':self.scene['id'],'source_file':self.scene['source_file'],
                                       'first_scene_frame_captured_at':self.scene_first_observed_at} if self.scene else None,
                      normal_pavement_detected=self.pavement_detected,ground_surface_class_ids=self.surface_labels,
                      ground_surface_observation=self.surface_observation,
                      ground_surface_basis="ADE20K semantic labels; not a path-safety assessment",
                      distance={"source":self.distance.source,"error":self.distance.error,
                                "fresh":bool(self.sample and self.sample.fresh(now)),
                                "sample":self.sample.to_dict() if self.sample else None},
                      capabilities={"pavement":"experimental","pothole":"experimental","visible_damage":"experimental",
                                    "puddle":"experimental" if "puddle" in self.ground_tasks else "required_unverified",
                                    "stairs":"experimental",
                                    "stair_scene_context":"experimental" if self.config.get("semantic",{}).get("stair_context_directory") else "disabled",
                                    "physical_unevenness":"required_unverified",
                                    "downward_stairs":"experimental" if "stairs" in self.ground_tasks else "required_unverified",
                                    "sudden_drop":"required_unverified"})
        if not self.objects_enabled:
            status.update(model="ground-vision",model_state="disabled",inference_ms=None)
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",default="config/camera.v2.toml")
    parser.add_argument("--distance",choices=["none","mock","pico"])
    parser.add_argument("--script")
    parser.add_argument("--replay",help="Explicit video replay instead of live camera")
    parser.add_argument("--no-audio",action="store_true")
    parser.add_argument("--mode",choices=["balanced","ground","awareness"],help="Awareness adds only dense-crowd person analysis to ground tasks")
    parser.add_argument("--models-root",type=Path,help="Use existing weights at this models directory")
    args = parser.parse_args()
    config = load_config(args.config,mode=args.mode,models_root=args.models_root)
    if args.distance: config["distance"]["mode"] = args.distance
    if args.script: config["distance"]["script"] = args.script
    if args.replay: config["source"] = {"video":args.replay}
    if args.no_audio: config["audio"]["enabled"] = False
    app = V2(config)
    server = ThreadingHTTPServer((config["server"]["host"],config["server"]["port"]),handler_for(app))
    def stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    app.start()
    try: server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__": main()
