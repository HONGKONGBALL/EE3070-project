import Combine
import CoreBluetooth
import Foundation
import UIKit

@MainActor
final class SessionStore: ObservableObject {
    @Published var destinationName = ""
    @Published var longitude = ""
    @Published var latitude = ""
    @Published private(set) var state = "IDLE"
    @Published private(set) var wifi = "待连接"
    @Published private(set) var mcp = "待连接"
    @Published private(set) var quality = "等待新位置"
    @Published private(set) var notice = "确认目的地并连接导航设备"
    @Published private(set) var active: Bool
    @Published private(set) var routeVersion: UInt64
    @Published private(set) var currentEvent: DeviceMessage?
    let location = LocationService()
    let ble: BLECentral
    let log = DiagnosticLog()
    var onPhysicalButton: ((PortableButton) -> Void)?
    var onNavigationFeedback: ((RouteFeedback) -> Void)?
    private let recovery: RecoveryStore
    private var snapshot: RecoverySnapshot
    private var eventReceivedAtMS: UInt64 = 0
    private var seenEventIDs: [UInt64] = []
    private var binding = BindHandshake()
    private var resumeRequested = false
    private var pendingCommands: [String: UInt64] = [:]
    private var clockEstimate: DeviceClockEstimate?
    private var heartbeat: Timer?
    private var lastButtonSequence: UInt64 = 0
    private var routeFeedback = RouteFeedbackTracker()
    private var initialRouteFailed = false

    init() {
        let localRecovery = RecoveryStore()
        let recovered = localRecovery.load()
        recovery = localRecovery
        snapshot = recovered
        active = recovered.active
        routeVersion = recovered.routeVersion
        ble = BLECentral(boundPeripheralID: recovered.boundPeripheralID)
        if recovered.active { notice = "会话已恢复，点击继续以更新位置"; state = "WAIT_FIX" }
        location.onSample = { [weak self] in self?.receiveFix($0) }
        location.onDiagnostic = { [weak self] in self?.log.write("location_state", $0) }
        ble.onMessage = { [weak self] message, link in self?.receive(message, from: link) }
        ble.onReady = { [weak self] in self?.linkReady() }
        ble.onLinkReset = { [weak self] in self?.linkReset() }
        ble.onDiagnostic = { [weak self] code in
            self?.log.write("ble_state", code)
            if code.hasPrefix("BLE_DISCONNECTED") { self?.currentEvent = nil }
        }
    }

    var destination: Destination? {
        guard let lng = Double(longitude), let lat = Double(latitude) else { return nil }
        return Destination(name: destinationName, lng: lng, lat: lat)
    }

    var bound: Bool { ble.boundPeripheralID != nil && ble.peripheral?.identifier == ble.boundPeripheralID }

    var canConfirm: Bool {
        guard let event = currentEvent, let issuedAt = event.issued_at_ms,
              let clockEstimate, clockEstimate.isUsable(at: NavigationClock.milliseconds),
              active else { return false }
        return event.isCurrent(for: snapshot.identity, routeVersion: routeVersion,
                               receivedAtMS: eventReceivedAtMS, nowMS: NavigationClock.milliseconds) &&
            clockEstimate.ageUpperBoundMS(issuedAtMS: issuedAt, nowMS: NavigationClock.milliseconds) < Double(min(event.ttl_ms ?? 0, 2000))
    }

    func connect(_ device: NavigationPeripheral) {
        ble.connect(device)
        if ble.boundPeripheralID == device.id {
            notice = "正在连接导航设备"
        } else {
            notice = "请核对 iPhone 系统配对弹窗与 USB 终端的六位数字，一致后在两端确认配对"
        }
    }

    func start(to destination: Destination) {
        guard UIApplication.shared.applicationState == .active,
              ble.ready, bound, !active else { return }
        snapshot.identity.sessionID = UUID().uuidString
        snapshot.identity.generation = 1
        routeVersion = 0
        routeFeedback = RouteFeedbackTracker()
        initialRouteFailed = false
        active = true
        currentEvent = nil
        seenEventIDs.removeAll()
        state = "WAIT_FIX"
        location.start()
        startHeartbeat()
        var command = snapshot.identity.command(.start)
        command.destination = destination
        send(command)
        persist()
    }

    func pause() { send(snapshot.identity.command(.pause)); currentEvent = nil }

    func resume() {
        guard active, UIApplication.shared.applicationState == .active else { return }
        resumeRequested = true
        currentEvent = nil
        location.start()
        startHeartbeat()
        send(snapshot.identity.command(.get_status))
        notice = "正在获取当前位置"
    }

    func cancel() {
        guard active else { return }
        let command = snapshot.identity.cancelCommand()
        ble.clearQueuedLocations()
        send(command)
        active = false
        initialRouteFailed = false
        state = "IDLE"
        currentEvent = nil
        resumeRequested = false
        location.stop()
        heartbeat?.invalidate()
        heartbeat = nil
        persist()
    }

    func repeatPrompt() { send(snapshot.identity.command(.repeat)) }

    func confirm() {
        guard canConfirm, let event = currentEvent else { repeatPrompt(); return }
        let operation: NavigationCommand
        switch state {
        case "CROSSING_WAIT": operation = .confirm_crossing
        case "ARRIVAL_NEAR": operation = .confirm_arrival
        default: operation = .confirm_turn
        }
        var command = snapshot.identity.command(operation)
        command.route_version = routeVersion
        command.event_id = event.event_id
        command.confirmation_source = "manual_confirm"
        send(command)
        currentEvent = nil
    }

    func sceneChanged(_ value: String) { log.write("app_lifecycle", value) }

    private func linkReady() {
        currentEvent = nil
        clockEstimate = nil
        ble.deviceClockEstimate = nil
        pendingCommands.removeAll()
        send(snapshot.identity.command(.get_status))
        if let link = ble.linkIdentity, link.peripheralID == ble.selectedPeripheralID,
           link.peripheralID != ble.boundPeripheralID {
            binding.begin(command: snapshot.identity.command(.bind), link: link)
        }
        notice = binding.commandID != nil ? "请核对 iPhone 与 USB 终端的六位数字，并在两端确认配对" :
            (active ? "连接已恢复，正在等待新位置" : "设备已连接，可确认目的地")
    }

    private func linkReset() {
        binding.reset()
        lastButtonSequence = 0
        pendingCommands.removeAll()
        currentEvent = nil
        clockEstimate = nil
        wifi = "等待设备状态"
        mcp = "等待设备状态"
    }

    private func retryBinding(for message: DeviceMessage, from link: BLELinkIdentity) {
        guard ble.ready else { return }
        let now = NavigationClock.milliseconds
        if let command = binding.command(for: message, from: link, at: now) {
            notice = "安全配对已确认，正在完成设备绑定"
            send(command)
        } else if binding.isExhausted(at: now) {
            notice = "请在电脑通过 USB 重新开启配对窗口，再选择导航设备完成绑定"
        }
    }

    private func receiveFix(_ sample: FixSample) {
        let frame = sample.frame(phoneSessionID: snapshot.identity.phoneSessionID, at: NavigationClock.milliseconds)
        log.write("location_measurement", frame)
        quality = sample.isFresh(at: NavigationClock.milliseconds) ? "有效位置 · 精度 \(Int(sample.accuracyM)) 米" : "定位质量更新中"
        guard active, ble.ready, bound else { return }
        ble.sendLocation(sample, phoneSessionID: snapshot.identity.phoneSessionID)
        if resumeRequested, sample.isFresh(at: NavigationClock.milliseconds),
           ble.deviceClockEstimate?.isUsable(at: NavigationClock.milliseconds) == true {
            resumeRequested = false
            send(snapshot.identity.command(.resume))
        }
    }

    private func send(_ input: CommandFrame) {
        guard ble.ready else { notice = "正在连接导航设备"; return }
        var command = input
        let sentAt = NavigationClock.milliseconds
        if command.command == .get_status { command.phone_send_monotonic_ms = sentAt }
        if pendingCommands.count >= 32, let oldest = pendingCommands.min(by: { $0.value < $1.value })?.key {
            pendingCommands.removeValue(forKey: oldest)
        }
        pendingCommands[command.command_id] = sentAt
        log.write("command_tx", command)
        ble.sendCommand(command)
    }

    private func receive(_ message: DeviceMessage, from link: BLELinkIdentity) {
        guard ble.linkIdentity == link else { return }
        log.write("ble_rx", message)
        if message.type == "physical_button" {
            guard ble.ready, bound,
                  let (key, sequence) = message.physicalButton(after: lastButtonSequence) else { return }
            lastButtonSequence = sequence
            guard UIApplication.shared.applicationState == .active else { return }
            if active {
                if key == .record, initialRouteFailed, routeVersion == 0 {
                    cancel()
                    onPhysicalButton?(key)
                    return
                }
                if key == .confirm && canConfirm { confirm() }
                if key == .next { repeatPrompt() }
            } else {
                onPhysicalButton?(key)
            }
            return
        }
        if message.type == "status", message.ble == "CONNECTED" {
            if let value = message.wifi { wifi = value }
            if let value = message.mcp { mcp = value }
        }
        retryBinding(for: message, from: link)
        if message.type == "ack" {
            guard let commandID = message.command_id,
                  pendingCommands[commandID] != nil || commandID == binding.commandID else { return }
            if let sentAt = pendingCommands.removeValue(forKey: commandID),
               message.phone_send_monotonic_ms == sentAt, let deviceAt = message.device_monotonic_ms {
                clockEstimate = DeviceClockEstimate(sentAtMS: sentAt, deviceAtMS: deviceAt,
                                                    receivedAtMS: NavigationClock.milliseconds)
                ble.deviceClockEstimate = clockEstimate
            }
            if let peripheralID = binding.acknowledge(message, from: link) {
                ble.boundPeripheralID = peripheralID
                snapshot.boundPeripheralID = peripheralID
                notice = "设备绑定完成，可确认目的地并开始导航"
                persist()
                send(snapshot.identity.command(.get_status))
            } else if message.accepted == false {
                notice = commandID == binding.commandID ? "请完成 iPhone 系统配对与电脑 USB 数字确认，设备正在等待授权" : "正在核对位置与当前导航提示"
            }
            return
        }
        guard message.session_id == snapshot.identity.sessionID,
              message.generation == snapshot.identity.generation else { return }
        if let version = message.route_version {
            guard version >= routeVersion else { return }
            if version != routeVersion { currentEvent = nil }
            routeVersion = version
        }
        if let status = message.state { state = status }
        if let value = message.quality { quality = value }
        if active, let feedback = routeFeedback.consume(message, identity: snapshot.identity,
            routeVersion: routeVersion, clock: clockEstimate, at: NavigationClock.milliseconds) {
            initialRouteFailed = feedback.kind == .failed && routeVersion == 0
            onNavigationFeedback?(feedback)
        }
        if (message.type == "maneuver" || message.type == "arrival_near"),
           message.ttl_ms != nil, message.issued_at_ms != nil,
           let eventID = message.event_id, !seenEventIDs.contains(eventID) {
            seenEventIDs.append(eventID)
            if seenEventIDs.count > 128 { seenEventIDs.removeFirst() }
            currentEvent = message
            eventReceivedAtMS = NavigationClock.milliseconds
            notice = message.type == "fault" ? (message.reason ?? "请检查设备状态") : "导航提示已更新"
        }
        if message.type == "arrived" {
            active = false
            state = "ARRIVED"
            currentEvent = nil
            location.stop()
            heartbeat?.invalidate()
            heartbeat = nil
            notice = "已确认到达入口"
        }
        persist()
    }

    private func persist() {
        snapshot.active = active
        snapshot.routeVersion = routeVersion
        do { try recovery.save(snapshot) }
        catch { log.write("recovery_io", "RECOVERY_\((error as NSError).code)") }
    }

    private func startHeartbeat() {
        heartbeat?.invalidate()
        heartbeat = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.active, self.ble.ready else { return }
                self.send(self.snapshot.identity.command(.get_status))
                self.log.write("heartbeat", "active")
            }
        }
    }
}
