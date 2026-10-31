"""Short UVC driver disconnect/rebind check; explicitly not a physical unplug test."""

import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request


def status():
    with urllib.request.urlopen('http://127.0.0.1:8000/status',timeout=2) as response:return json.load(response)


def compact(s):
    return {k:s.get(k) for k in ('ready','error','run_id','input_source','camera','detections','tasks')}


def bind(action,interface):
    subprocess.run(['sudo','-n','tee','/sys/bus/usb/drivers/uvcvideo/'+action],
                   input=interface,text=True,stdout=subprocess.DEVNULL,check=True,timeout=4)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disconnect',action='store_true',help='Perform a 4-second software disconnect of the verified Logitech C270 video interface')
    parser.add_argument('--out',type=Path,default=Path('logs/v2-camera-driver-recovery.json'))
    args=parser.parse_args()
    before=status()
    if not before['ready'] or before.get('input_source')!='live_camera':raise RuntimeError('A healthy live camera is required')
    node=Path(before['camera']['device']).resolve()
    if not re.fullmatch(r'video\d+',node.name):raise ValueError('Expected a V4L video node')
    interface_path=(Path('/sys/class/video4linux')/node.name/'device').resolve()
    interface=interface_path.name
    if not re.fullmatch(r'\d+-[\d.]+:\d+\.\d+',interface):raise ValueError('Expected a USB interface')
    if (interface_path/'driver').resolve().name!='uvcvideo':raise ValueError('Only the UVC video driver may be rebound')
    if (interface_path.parent/'idVendor').read_text().strip()!='046d' or (interface_path.parent/'idProduct').read_text().strip()!='0825':
        raise ValueError('Only the already identified Logitech C270 is supported')
    if not args.disconnect:
        print(json.dumps({'verified_interface':interface,'device':str(node),'no_change':True}));return
    permission=subprocess.run(['sudo','-n','true'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=3)
    if permission.returncode:
        report={'type':'software_uvc_disconnect_rebind','performed':False,'passed':False,
                'blocked':'No non-interactive privilege to unbind the UVC interface; no device changed',
                'verified_interface':interface}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2));raise SystemExit(1)
    log=Path('logs')/('camera-'+before['run_id']+'.jsonl')
    original_lines=len(log.read_text().splitlines())
    report={'type':'software_uvc_disconnect_rebind','not_physical_unplug':True,'interface':interface,
            'before':compact(before),'during':[],'completed':False,'started_at':time.time()}
    changed=False
    def interrupted(*_):raise RuntimeError('Recovery check interrupted')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):signal.signal(sig,interrupted)
    try:
        changed=True  # Even a timeout after tee wrote the interface must enter the restore path.
        bind('unbind',interface)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            time.sleep(.25)
            report['during'].append(compact(status()))
        report['visual_evidence_cleared']=any(not s['camera']['ready'] and not s['detections'] for s in report['during'])
        try:
            with urllib.request.urlopen('http://127.0.0.1:8000/frame.jpg',timeout=2) as response:
                report['stale_frame_http_status']=response.status
        except urllib.error.HTTPError as exc:report['stale_frame_http_status']=exc.code
    finally:
        if changed:
            current_driver=interface_path/'driver'
            if not current_driver.exists():bind('bind',interface)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            time.sleep(.25)
            after=status()
            if after['ready'] and all(t['state'] in ('ready','disabled') for t in after.get('tasks',{}).values()):
                report['after']=compact(after)
                report['completed']=after['run_id']==before['run_id'] and after['camera']['frame_id']>before['camera']['frame_id']
                break
        rows=[json.loads(line) for line in log.read_text().splitlines()[original_lines:]]
        report['records']=rows
        report['elapsed_s']=round(time.time()-report['started_at'],2)
        report['passed']=bool(report['completed'] and report.get('visual_evidence_cleared') and report.get('stale_frame_http_status')==503)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('before','during','after','records')},indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
