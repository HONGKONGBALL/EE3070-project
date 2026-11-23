import AVFoundation
import Combine
import Foundation
import UIKit

@MainActor
final class PortableButtonFlow: NSObject, ObservableObject, @preconcurrency AVSpeechSynthesizerDelegate {
    @Published private(set) var status = "按 K2 说出目的地，按 K3 切换候选，按 K1 确认"
    @Published private(set) var selected: PortablePlace?
    @Published private(set) var candidateNumber = 0
    let speech: PortableSpeech
    let places: PortablePlaceSearch
    var onConfirm: (@MainActor (PortablePlace) -> Void)?
    var onDiagnostic: (@MainActor (PortableStage) -> Void)?

    private enum Phase { case idle, recording, searching, choosing, submitted }
    private var phase = Phase.idle
    private let speaker = AVSpeechSynthesizer()
    private var observations = Set<AnyCancellable>()
    private var generation = UUID()
    private var recordingDelay: Task<Void, Never>?
    private var interpretation: Task<Void, Never>?
    private var recordingPrompt: AVSpeechUtterance?
    private var options: [PortablePlace] = []
    private var expandedOptions = false
    private var announcedID: String?
    private var announcement: AVSpeechUtterance?
    private var announcementID: String?
    private var lastSpeechStatus = ""
    private var routeAnnouncement: AVSpeechUtterance?
    private var routeFeedback: RouteFeedback?

    init(speech: PortableSpeech? = nil, places: PortablePlaceSearch? = nil) {
        self.speech = speech ?? PortableSpeech()
        self.places = places ?? PortablePlaceSearch()
        super.init()
        speaker.delegate = self
        speaker.usesApplicationAudioSession = false
        self.speech.onStage = { [weak self] in self?.audit($0) }
        self.places.objectWillChange.sink { [weak self] _ in
            Task { @MainActor [weak self] in self?.observeSearch() }
        }.store(in: &observations)
        self.speech.objectWillChange.sink { [weak self] _ in
            Task { @MainActor [weak self] in self?.observeSpeech() }
        }.store(in: &observations)
    }

    var canConfirm: Bool {
        guard phase == .choosing, !speech.listening, !speech.working,
              !places.searching, let selected, announcedID == selected.id else { return false }
        return selected.wireDestination != nil
    }

    func handle(_ button: String) {
        switch button {
        case "K1": confirmSelected()
        case "K2": toggleRecording()
        case "K3": nextCandidate()
        default: break
        }
    }

    func cancel() {
        recordingDelay?.cancel()
        recordingDelay = nil
        interpretation?.cancel()
        interpretation = nil
        speech.cancel()
        generation = UUID()
        routeFeedback = nil
        places.cancel()
        stopSpeaking()
        options = []
        expandedOptions = false
        selected = nil
        candidateNumber = 0
        phase = .idle
        status = "按 K2 说出目的地"
    }

    func receiveNavigationFeedback(_ feedback: RouteFeedback) {
        routeFeedback = feedback
        if feedback.kind == .ready {
            status = "步行路线已规划完成。"
        } else {
            switch feedback.reason {
            case let reason? where reason.hasPrefix("COORDINATE_"):
                status = "路线坐标需要校验。请核对路线的坐标配置。"
            case "MCP_QUOTA":
                status = "地图路线服务额度已用完。请稍后再试。"
            case "MCP_TIMEOUT", "MCP_TRANSPORT":
                status = "路线服务暂未响应。请检查设备网络后再试。"
            case "ROUTE_ERROR":
                status = "地图未返回可用的步行路线。请按 K2 重新选择目的地。"
            default:
                status = feedback.routeVersion == 0 ? "路线规划遇到问题。请按 K2 重新选择目的地。" : "路线更新遇到问题。"
            }
        }
        audit(feedback.kind == .ready ? "route_ready" : "route_error")
        guard UIApplication.shared.applicationState == .active else { return }
        say(status)
        routeAnnouncement = announcement
    }

    private func toggleRecording() {
        if phase == .recording {
            guard recordingPrompt == nil, recordingDelay == nil, !speech.working else { return }
            if speech.canFinish {
                let token = generation
                audit("recording_end_requested")
                speech.toggle { [weak self] in self?.search($0, token: token) }
                return
            }
        }
        cancel()
        phase = .recording
        audit("recording_start_requested")
        lastSpeechStatus = ""
        status = "正在准备语音输入"
        say("请说出目的地，说完再按一次 K2。")
        recordingPrompt = announcement
    }

    private func startRecordingAfterPrompt() {
        let token = generation
        recordingDelay = Task { @MainActor [weak self] in
            guard let self else { return }
            do { try await Task.sleep(for: .milliseconds(100)) }
            catch { return }
            guard generation == token, !Task.isCancelled else { return }
            recordingDelay = nil
            speech.toggle { [weak self] in self?.search($0, token: token) }
        }
    }

    private func search(_ text: String, token: UUID?) {
        guard token == generation, phase == .recording else { return }
        phase = .searching
        audit("place_search_requested")
        announcedID = nil
        selected = nil
        status = "正在查找目的地"
        say(status)
        interpretation = Task { @MainActor [weak self] in
            let intent = await GrokDestinationInterpreter.shared.interpret(text)
            guard let self, !Task.isCancelled, token == generation, phase == .searching else { return }
            interpretation = nil
            audit(intent == nil ? "local_destination_query" : "grok_destination_query")
            places.search(intent?.keywords ?? text, transcript: text, interpretedNearby: intent?.nearby == true)
        }
    }

    private func observeSpeech() {
        guard phase == .recording, recordingPrompt == nil, recordingDelay == nil else { return }
        let prompt: String
        switch speech.status {
        case "识别已完成，再点一次确认": prompt = "再按 K2 确认语音目的地"
        case "请使用文字输入目的地": prompt = "请检查手机本地语音设置，再按 K2 重试"
        case "请稍后重试语音，或输入目的地": prompt = "请稍后按 K2 重试语音"
        case "请再说一次目的地", "点按麦克风说出目的地": prompt = "请按 K2 再说一次目的地"
        default: prompt = speech.status
        }
        status = speech.listening ? "请说出目的地，再按 K2 结束" : prompt
        if !speech.listening, !speech.working, speech.status != lastSpeechStatus {
            say(status)
        }
        lastSpeechStatus = speech.status
    }

    private func observeSearch() {
        guard phase == .searching, interpretation == nil, !places.searching else { return }
        options = Array(places.candidates.filter {
            !$0.id.isEmpty && $0.wireDestination != nil
        }.prefix(5))
        guard !options.isEmpty else {
            phase = .idle
            status = "\(places.status)。按 K2 重试地点搜索"
            say(status)
            return
        }
        phase = .choosing
        expandedOptions = places.expandedSearch
        candidateNumber = 1
        audit("candidates_ready")
        announceSelected()
    }

    private func nextCandidate() {
        guard phase != .recording else { return }
        guard phase == .choosing, !options.isEmpty else {
            if !speech.listening, !speech.working {
                say(phase == .searching ? "正在查找目的地，请稍候" : "请先按 K2 说出目的地")
            }
            return
        }
        candidateNumber = candidateNumber % options.count + 1
        announceSelected()
    }

    private func announceSelected() {
        stopSpeaking()
        let place = options[candidateNumber - 1]
        selected = place
        audit("candidate_selected")
        let distance: String
        if let meters = place.distanceMeters, meters.isFinite, meters >= 0 {
            distance = meters < 1_000 ? "直线距离约 \(Int(meters.rounded())) 米" :
                "直线距离约 \(String(format: "%.1f", meters / 1_000)) 公里"
        } else {
            distance = ""
        }
        status = (["候选 \(candidateNumber)：\(place.name)", distance, place.address]
            .filter { !$0.isEmpty }).joined(separator: " · ")
        let address = place.address.isEmpty ? "" : "，地址，\(place.address)"
        let distancePrompt = distance.isEmpty ? "" : "，\(distance)"
        let rangePrompt = candidateNumber == 1 && expandedOptions ? "已扩大搜索范围。" : ""
        say("\(rangePrompt)第 \(candidateNumber) 个候选，共 \(options.count) 个。\(place.name)\(distancePrompt)\(address)。按 K1 确认，按 K3 下一处。",
            candidateID: place.id)
    }

    private func confirmSelected() {
        guard canConfirm, let selected, let onConfirm else {
            if phase == .idle { say("请先按 K2 说出目的地") }
            return
        }
        phase = .submitted
        audit("destination_confirmation_requested")
        status = "已确认目的地，正在请求导航"
        say(status)
        onConfirm(selected)
    }

    private func say(_ text: String, candidateID: String? = nil) {
        guard !speech.listening, !speech.working, recordingDelay == nil else { return }
        stopSpeaking()
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "zh-CN")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        announcement = utterance
        announcementID = candidateID
        speaker.speak(utterance)
    }

    private func stopSpeaking() {
        routeAnnouncement = nil
        recordingPrompt = nil
        announcement = nil
        announcementID = nil
        announcedID = nil
        _ = speaker.stopSpeaking(at: .immediate)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        if utterance === routeAnnouncement { audit("route_feedback_speech_finished"); return }
        if utterance === recordingPrompt, phase == .recording {
            audit("recording_prompt_spoken")
            recordingPrompt = nil
            startRecordingAfterPrompt()
            return
        }
        guard utterance === announcement, phase == .choosing,
              let id = announcementID, selected?.id == id else { return }
        announcedID = id
        audit("candidate_spoken")
        status = "\(selected?.name ?? "候选地点")，按 K1 确认或 K3 下一处"
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        if utterance === recordingPrompt {
            recordingPrompt = nil
            status = "请再按 K2 开始语音输入"
        }
        if utterance === announcement { announcedID = nil }
    }

    private func audit(_ stage: String) {
        onDiagnostic?(PortableStage(stage: stage, attempt_id: generation.uuidString,
            candidate_ordinal: candidateNumber > 0 ? candidateNumber : nil,
            candidate_count: options.isEmpty ? nil : options.count,
            route_version: routeFeedback?.routeVersion, event_id: routeFeedback?.eventID))
    }
}
