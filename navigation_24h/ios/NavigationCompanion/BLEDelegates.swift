import CoreBluetooth
import Foundation

extension BLECentral: @preconcurrency CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            prepareRememberedDevice()
        case .poweredOff: connection = "请开启蓝牙"; clearLink()
        case .unauthorized: connection = "请在设置中允许蓝牙"; clearLink()
        case .unsupported: connection = "当前设备蓝牙状态受限"; clearLink()
        case .resetting: connection = "蓝牙正在恢复"; clearLink()
        case .unknown: connection = "蓝牙准备中"
        @unknown default: connection = "蓝牙状态已更新"
        }
    }

    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        guard let candidates = dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral] else { return }
        for candidate in candidates {
            guard candidate.identifier == boundPeripheralID else {
                central.cancelPeripheralConnection(candidate)
                continue
            }
            onDiagnostic?("BLE_RESTORE_KNOWN")
            prepareConnection(candidate)
        }
        onDiagnostic?("BLE_RESTORED")
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        discovered[peripheral.identifier] = peripheral
        devices.removeAll { $0.id == peripheral.identifier }
        devices.append(NavigationPeripheral(id: peripheral.identifier,
                                             name: peripheral.name ?? "导航设备", rssi: RSSI.intValue))
        connectDiscoveredRemembered(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard self.peripheral === peripheral else { return }
        onDiagnostic?("BLE_CONNECTED")
        prepareConnection(peripheral)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral === peripheral else { return }
        clearLink()
        connection = "连接待重试"
        onDiagnostic?("BLE_CONNECT_\((error as NSError?)?.code ?? 0)")
        recordError(error, context: "CONNECT")
        if peripheral.identifier == boundPeripheralID { prepareConnection(peripheral) }
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral === peripheral else { return }
        clearLink()
        connection = "设备连接恢复中"
        onDiagnostic?("BLE_DISCONNECTED_\((error as NSError?)?.code ?? 0)")
        recordError(error, context: "DISCONNECTED")
        if central.state == .poweredOn, peripheral.identifier == boundPeripheralID {
            prepareConnection(peripheral)
        }
    }
}

extension BLECentral: @preconcurrency CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard self.peripheral === peripheral, discoveryInProgress else { return }
        guard error == nil, let services = peripheral.services else {
            discoveryInProgress = false
            onDiagnostic?("BLE_SERVICE_DISCOVERY")
            recordError(error, context: "SERVICE_DISCOVERY"); return
        }
        for service in services where service.uuid == NavigationGATT.service {
            if requestedServices.insert(ObjectIdentifier(service)).inserted {
                peripheral.discoverCharacteristics(nil, for: service)
            }
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard self.peripheral === peripheral, discoveryInProgress,
              requestedServices.contains(ObjectIdentifier(service)) else { return }
        guard error == nil, let discovered = service.characteristics else {
            discoveryInProgress = false
            onDiagnostic?("BLE_CHARACTERISTIC_DISCOVERY")
            recordError(error, context: "CHARACTERISTIC_DISCOVERY"); return
        }
        for characteristic in discovered {
            characteristics[characteristic.uuid] = characteristic
            switch characteristic.uuid {
            case NavigationGATT.status, NavigationGATT.event:
                if characteristic.isNotifying {
                    notifications.insert(characteristic.uuid)
                    onDiagnostic?("BLE_NOTIFY_RESTORED")
                }
                peripheral.setNotifyValue(true, for: characteristic)
            case NavigationGATT.capabilities: peripheral.readValue(for: characteristic)
            default: break
            }
        }
        checkReady()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral === peripheral, characteristics[characteristic.uuid] === characteristic else { return }
        guard error == nil, characteristic.isNotifying else {
            notifications.remove(characteristic.uuid)
            resetReadiness()
            connection = "等待导航通知通道"
            onDiagnostic?("BLE_NOTIFY_SUBSCRIBE")
            recordError(error, context: "NOTIFY_SUBSCRIBE"); return
        }
        notifications.insert(characteristic.uuid)
        checkReady()
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral === peripheral, characteristics[characteristic.uuid] === characteristic else { return }
        writing = false
        if let error {
            fragments.removeAll()
            onDiagnostic?("BLE_WRITE_\((error as NSError).code)")
            recordError(error, context: "WRITE")
        }
        pump()
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral === peripheral, characteristics[characteristic.uuid] === characteristic,
              let link = linkIdentity else { return }
        guard error == nil, let value = characteristic.value else {
            onDiagnostic?("BLE_READ")
            recordError(error, context: "READ"); return
        }
        do {
            if characteristic.uuid == NavigationGATT.capabilities {
                struct Capabilities: Decodable { let protocol_version: Int }
                let capabilities = try JSONDecoder().decode(Capabilities.self, from: value)
                capabilitiesValid = capabilities.protocol_version == 1
                checkReady()
                return
            }
            var receiver = reassemblers[characteristic.uuid] ?? FrameReassembler()
            let message = try receiver.receive(value, at: NavigationClock.milliseconds)
            reassemblers[characteristic.uuid] = receiver
            if let message {
                let decoded = try JSONDecoder().decode(DeviceMessage.self, from: message)
                if decoded.type == "status" || decoded.type == "ack" {
                    guard characteristic.uuid == NavigationGATT.status else { return }
                }
                if decoded.type == "physical_button" && characteristic.uuid != NavigationGATT.event {
                    return
                }
                if decoded.type == "status", decoded.ble == "CONNECTED", channelsPrepared,
                   linkIdentity == link {
                    trustedStatusReceived = true
                    checkReady()
                }
                onMessage?(decoded, link)
            }
        } catch {
            reassemblers[characteristic.uuid] = FrameReassembler()
            onDiagnostic?("BLE_RECEIVE_\(error)")
        }
    }
}
