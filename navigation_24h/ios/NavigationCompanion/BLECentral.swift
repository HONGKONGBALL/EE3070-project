import CoreBluetooth
import Combine
import Foundation

struct NavigationPeripheral: Identifiable {
    let id: UUID
    let name: String
    let rssi: Int
}

enum NavigationGATT {
    static let service = CBUUID(string: "8F7D0001-7B1E-4F29-8B21-000000003070")
    static let location = CBUUID(string: "8F7D0002-7B1E-4F29-8B21-000000003070")
    static let command = CBUUID(string: "8F7D0003-7B1E-4F29-8B21-000000003070")
    static let status = CBUUID(string: "8F7D0004-7B1E-4F29-8B21-000000003070")
    static let event = CBUUID(string: "8F7D0005-7B1E-4F29-8B21-000000003070")
    static let capabilities = CBUUID(string: "8F7D0006-7B1E-4F29-8B21-000000003070")
}

enum PendingTransmission {
    case location(FixSample, String)
    case command(CommandFrame)
}

@MainActor
final class BLECentral: NSObject, ObservableObject {
    @Published var connection = "蓝牙准备中"
    @Published var devices: [NavigationPeripheral] = []
    @Published var ready = false
    var onMessage: ((DeviceMessage, BLELinkIdentity) -> Void)?
    var onReady: (() -> Void)?
    var onLinkReset: (() -> Void)?
    var onDiagnostic: ((String) -> Void)?
    var boundPeripheralID: UUID?
    var selectedPeripheralID: UUID?
    var central: CBCentralManager!
    var peripheral: CBPeripheral?
    var discovered: [UUID: CBPeripheral] = [:]
    var characteristics: [CBUUID: CBCharacteristic] = [:]
    var reassemblers: [CBUUID: FrameReassembler] = [:]
    var notifications: Set<CBUUID> = []
    var capabilitiesValid = false
    var channelsPrepared = false
    var trustedStatusReceived = false
    var pending: [PendingTransmission] = []
    var fragments: [Data] = []
    var currentCharacteristic: CBCharacteristic?
    var writing = false
    var messageID: UInt16 = 0
    var deviceClockEstimate: DeviceClockEstimate?
    var discoveryInProgress = false
    var connectionRequested = false
    var requestedServices = Set<ObjectIdentifier>()
    private var linkGeneration: UInt64 = 0

    var linkIdentity: BLELinkIdentity? {
        peripheral.map { BLELinkIdentity(peripheralID: $0.identifier, generation: linkGeneration) }
    }

    init(boundPeripheralID: UUID?) {
        self.boundPeripheralID = boundPeripheralID
        super.init()
        central = CBCentralManager(delegate: self, queue: .main, options: [
            CBCentralManagerOptionRestoreIdentifierKey: "navigation.central.v1",
            CBCentralManagerOptionShowPowerAlertKey: true
        ])
    }

    func scan() {
        guard central.state == .poweredOn, !ready else { return }
        connection = "搜索导航设备"
        central.scanForPeripherals(withServices: [NavigationGATT.service], options: nil)
    }

    func connect(_ device: NavigationPeripheral) {
        guard let candidate = discovered[device.id] else { return }
        selectedPeripheralID = device.id
        if peripheral === candidate { clearLink() }
        prepareConnection(candidate)
    }

    func prepareConnection(_ candidate: CBPeripheral) {
        if peripheral !== candidate {
            let previous = peripheral
            clearLink()
            peripheral = candidate
            if let previous { central.cancelPeripheralConnection(previous) }
        }
        candidate.delegate = self
        guard central.state == .poweredOn else { return }
        central.stopScan()
        switch candidate.state {
        case .connected:
            connectionRequested = false
            guard !ready, !channelsPrepared, !discoveryInProgress else { return }
            discoveryInProgress = true
            requestedServices.removeAll()
            characteristics.removeAll()
            notifications.removeAll()
            capabilitiesValid = false
            connection = "检查导航服务"
            onDiagnostic?("BLE_DISCOVERY_STARTED")
            candidate.discoverServices([NavigationGATT.service])
        case .connecting:
            connectionRequested = true
            connection = "恢复导航设备连接"
        case .disconnected, .disconnecting:
            guard !connectionRequested else { return }
            connectionRequested = true
            connection = "连接已选择的导航设备"
            onDiagnostic?("BLE_CONNECTION_REQUESTED")
            central.connect(candidate, options: nil)
        @unknown default:
            onDiagnostic?("BLE_CONNECTION_STATE")
        }
    }

    func prepareRememberedDevice() {
        if let current = peripheral,
           current.identifier == selectedPeripheralID || current.identifier == boundPeripheralID {
            prepareConnection(current)
        } else if let id = boundPeripheralID,
                  let known = central.retrievePeripherals(withIdentifiers: [id]).first {
            prepareConnection(known)
        } else { scan() }
    }

    func connectDiscoveredRemembered(_ candidate: CBPeripheral) {
        guard candidate.identifier == boundPeripheralID,
              selectedPeripheralID == nil || selectedPeripheralID == boundPeripheralID,
              peripheral == nil || peripheral?.identifier == boundPeripheralID else { return }
        if let current = peripheral, current.state == .connected || current.state == .connecting {
            prepareConnection(current)
        } else { prepareConnection(candidate) }
    }

    func sendCommand(_ frame: CommandFrame) {
        guard ready else { onDiagnostic?("BLE_NOT_READY"); return }
        guard pending.count < 32 else { onDiagnostic?("BLE_QUEUE_CAPACITY"); return }
        pending.append(.command(frame))
        pump()
    }

    func sendLocation(_ sample: FixSample, phoneSessionID: String) {
        guard ready,
              sample.transportFrame(phoneSessionID: phoneSessionID, at: NavigationClock.milliseconds,
                                    clock: deviceClockEstimate) != nil else { return }
        pending.removeAll { item in
            if case .location = item { return true }
            return false
        }
        guard pending.count < 32 else { onDiagnostic?("BLE_QUEUE_CAPACITY"); return }
        pending.append(.location(sample, phoneSessionID))
        pump()
    }

    func clearQueuedLocations() {
        pending.removeAll { item in
            if case .location = item { return true }
            return false
        }
    }

    func pump() {
        guard ready, !writing, let peripheral else { return }
        if fragments.isEmpty {
            guard !pending.isEmpty else { return }
            let next = pending.removeFirst()
            do {
                let data: Data
                let channel: CBUUID
                switch next {
                case let .location(sample, phoneSessionID):
                    let sentAt = NavigationClock.milliseconds
                    guard let frame = sample.transportFrame(phoneSessionID: phoneSessionID,
                                                            at: sentAt, clock: deviceClockEstimate)
                    else { pump(); return }
                    data = try JSONEncoder().encode(frame)
                    channel = NavigationGATT.location
                case let .command(command):
                    data = try JSONEncoder().encode(command)
                    channel = NavigationGATT.command
                }
                messageID &+= 1
                fragments = try FrameCodec.fragments(data, messageID: messageID,
                    maximumWriteLength: peripheral.maximumWriteValueLength(for: .withResponse))
                currentCharacteristic = characteristics[channel]
            } catch {
                onDiagnostic?("BLE_ENCODE_\(error)")
                pump()
                return
            }
        }
        guard let characteristic = currentCharacteristic, !fragments.isEmpty else { return }
        writing = true
        peripheral.writeValue(fragments.removeFirst(), for: characteristic, type: .withResponse)
    }

    func clearLink() {
        resetReadiness()
        characteristics.removeAll()
        notifications.removeAll()
        capabilitiesValid = false
        discoveryInProgress = false
        connectionRequested = false
        requestedServices.removeAll()
    }

    func resetReadiness() {
        linkGeneration &+= 1
        ready = false
        channelsPrepared = false
        trustedStatusReceived = false
        reassemblers.removeAll()
        pending.removeAll()
        fragments.removeAll()
        currentCharacteristic = nil
        writing = false
        deviceClockEstimate = nil
        onLinkReset?()
    }

    func checkReady() {
        guard !ready, capabilitiesValid,
              characteristics[NavigationGATT.location] != nil,
              characteristics[NavigationGATT.command] != nil,
              notifications.contains(NavigationGATT.status),
              notifications.contains(NavigationGATT.event) else { return }
        if !channelsPrepared {
            channelsPrepared = true
            trustedStatusReceived = false
            discoveryInProgress = false
            connection = "等待导航设备状态"
            onDiagnostic?("BLE_CHANNELS_PREPARED")
        }
        guard trustedStatusReceived else { return }
        ready = true
        connection = "已连接 · 通道就绪"
        onDiagnostic?("BLE_READY")
        onReady?()
    }

    func recordError(_ error: Error?, context: String) {
        guard let error = error as NSError? else { return }
        onDiagnostic?("BLE_ERROR_DETAIL_\(context)_\(error.domain)_\(error.code)")
    }
}
