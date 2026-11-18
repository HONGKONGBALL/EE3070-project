import AVFoundation
import Combine
import Foundation
import Speech
import UIKit

@MainActor
final class PortableSpeech: ObservableObject {
    @Published private(set) var listening = false
    @Published private(set) var working = false
    @Published private(set) var status = "点按麦克风说出目的地"
    var onStage: (@MainActor (String) -> Void)?

    private var session = UUID()
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognition: SFSpeechRecognitionTask?
    private var recognizer: SFSpeechRecognizer?
    private var authorizationTask: Task<Void, Never>?
    private var finishTimeout: Task<Void, Never>?
    private var callback: (@MainActor (String) -> Void)?
    private var bestText = ""
    private var manualFinish = false
    private var awaitingConfirmation = false
    private var tapInstalled = false
    private var audioSessionActive = false

    var canFinish: Bool { listening || awaitingConfirmation }

    func preparePermissions() {
        guard !listening, !working else { return }
        begin(onText: { _ in }, capture: false)
    }

    func toggle(onText: @escaping @MainActor (String) -> Void) {
        if awaitingConfirmation {
            complete(session)
        } else if listening {
            finishRecording()
        } else if !working {
            begin(onText: onText)
        }
    }

    func cancel() {
        session = UUID()
        releaseResources()
        callback = nil
        bestText = ""
        manualFinish = false
        awaitingConfirmation = false
        listening = false
        working = false
        status = "点按麦克风说出目的地"
    }

    private func begin(onText: @escaping @MainActor (String) -> Void, capture: Bool = true) {
        cancel()
        guard UIApplication.shared.applicationState == .active else {
            status = "请打开步行导航，再按 K2 开始语音输入"
            onStage?("speech_foreground_required")
            return
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN")),
              recognizer.supportsOnDeviceRecognition else {
            status = "请使用文字输入目的地"
            return
        }
        let token = session
        self.recognizer = recognizer
        callback = onText
        working = true
        status = "正在请求语音与麦克风授权"
        onStage?("speech_permissions_requested")
        authorizationTask = Task { @MainActor [weak self] in
            let speechPermission = await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
            }
            guard let self, self.session == token, !Task.isCancelled else { return }
            guard speechPermission == .authorized else {
                self.onStage?("speech_permission_required")
                self.abort("请在系统设置中允许语音识别")
                return
            }
            let microphoneAllowed = await withCheckedContinuation { continuation in
                AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
            }
            guard self.session == token, !Task.isCancelled else { return }
            guard microphoneAllowed else {
                self.onStage?("microphone_permission_required")
                self.abort("请在系统设置中允许麦克风")
                return
            }
            if !capture {
                self.working = false
                self.callback = nil
                self.status = "语音权限已准备好，按 K2 开始录音"
                self.onStage?("speech_permissions_ready")
                return
            }
            for _ in 0..<40 where UIApplication.shared.applicationState == .inactive {
                do { try await Task.sleep(for: .milliseconds(50)) }
                catch { return }
            }
            guard self.session == token, !Task.isCancelled else { return }
            self.startAudio(recognizer: recognizer, token: token)
        }
    }

    private func startAudio(recognizer: SFSpeechRecognizer, token: UUID) {
        guard UIApplication.shared.applicationState == .active else {
            onStage?("speech_foreground_required")
            abort("请打开步行导航，再按 K2 开始语音输入")
            return
        }
        guard recognizer.supportsOnDeviceRecognition, recognizer.isAvailable else {
            abort("请稍后重试语音，或输入目的地")
            return
        }
        do {
            let audioSession = AVAudioSession.sharedInstance()
            try audioSession.setCategory(.record, mode: .measurement)
            try audioSession.setActive(true)
            audioSessionActive = true
            let engine = AVAudioEngine()
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.requiresOnDeviceRecognition = true
            request.shouldReportPartialResults = true
            request.taskHint = .search
            self.engine = engine
            self.request = request
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                abort("请检查麦克风输入后重试")
                return
            }
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
                request.append(buffer)
            }
            tapInstalled = true
            recognition = recognizer.recognitionTask(with: request) { [weak self] result, error in
                let text = result?.bestTranscription.formattedString
                let terminal = result?.isFinal == true || error != nil
                Task { @MainActor [weak self] in
                    self?.receive(text: text, terminal: terminal, token: token)
                }
            }
            engine.prepare()
            try engine.start()
            working = false
            listening = true
            status = "请说出目的地，再点一次结束"
            onStage?("microphone_started")
        } catch {
            onStage?("microphone_start_failed")
            abort("请检查麦克风输入后重试")
        }
    }

    private func receive(text: String?, terminal: Bool, token: UUID) {
        guard token == session, !awaitingConfirmation else { return }
        if let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            bestText = text
        }
        guard terminal else { return }
        if manualFinish {
            complete(token)
        } else {
            stopAudio()
            recognition = nil
            request = nil
            listening = false
            working = false
            awaitingConfirmation = !bestText.isEmpty
            status = awaitingConfirmation ? "识别已完成，再点一次确认" : "请再说一次目的地"
            if !awaitingConfirmation { abort(status) }
        }
    }

    private func finishRecording() {
        let token = session
        manualFinish = true
        listening = false
        working = true
        status = "正在整理识别文字"
        stopAudio()
        request?.endAudio()
        recognition?.finish()
        finishTimeout = Task { @MainActor [weak self] in
            do { try await Task.sleep(for: .seconds(3)) }
            catch { return }
            self?.complete(token)
        }
    }

    private func complete(_ token: UUID) {
        guard token == session else { return }
        let text = bestText.trimmingCharacters(in: .whitespacesAndNewlines)
        let completion = callback
        cancel()
        status = text.isEmpty ? "请再说一次目的地" : "已识别目的地"
        onStage?(text.isEmpty ? "speech_finished_empty" : "transcript_ready")
        if !text.isEmpty { completion?(text) }
    }

    private func abort(_ message: String) {
        cancel()
        status = message
    }

    private func stopAudio() {
        let wasRunning = engine?.isRunning == true
        engine?.stop()
        if tapInstalled {
            engine?.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        if audioSessionActive {
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            audioSessionActive = false
        }
        if wasRunning { onStage?("microphone_stopped") }
    }

    private func releaseResources() {
        authorizationTask?.cancel()
        authorizationTask = nil
        finishTimeout?.cancel()
        finishTimeout = nil
        stopAudio()
        request?.endAudio()
        recognition?.cancel()
        recognition = nil
        request = nil
        engine = nil
        recognizer = nil
    }
}
