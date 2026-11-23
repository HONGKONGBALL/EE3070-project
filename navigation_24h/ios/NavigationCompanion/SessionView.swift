import SwiftUI
import UIKit

@MainActor
struct SessionView: View {
    @ObservedObject var session: SessionStore
    @ObservedObject var macLocation: MacLocationSharing
    @StateObject private var buttons = PortableButtonFlow()
    @Environment(\.scenePhase) private var phase

    var body: some View {
        NavigationStack {
            Form {
                PortableDestinationPanel(session: session, ble: session.ble,
                                         places: buttons.places, speech: buttons.speech,
                                         buttonStatus: buttons.status)
                if macLocation.pairedHost != nil {
                    MacLocationPanel(sharing: macLocation)
                }
                DevicePanel(ble: session.ble, location: session.location, connect: session.connect)
                Section("导航会话") {
                    LabeledContent("状态", value: session.state)
                    LabeledContent("路线版本", value: String(session.routeVersion))
                    LabeledContent("位置", value: session.quality)
                    LabeledContent("Wi-Fi", value: session.wifi)
                    LabeledContent("路线服务", value: session.mcp)
                    Text(session.notice).accessibilityAddTraits(.updatesFrequently)
                    HStack {
                        Button("暂停", action: session.pause).disabled(!session.active)
                        Spacer()
                        Button("继续", action: session.resume).disabled(!session.active)
                    }
                    Button("重播当前提示", action: session.repeatPrompt).disabled(!session.active)
                    Button("确认当前路口或入口", action: session.confirm).disabled(!session.active)
                        .accessibilityHint("确认当前有效的路口、过街或入口提示")
                    Button("结束导航", role: .destructive, action: session.cancel).disabled(!session.active)
                }
                Section("会话记录") {
                    ShareLink(item: session.log.url) { Label("导出导航记录", systemImage: "square.and.arrow.up") }
                        .accessibilityLabel("导出导航会话记录")
                }
            }
            .navigationTitle("步行导航")
        }
        .onAppear(perform: connectButtons)
        .onDisappear {
            session.onPhysicalButton = nil
            session.onNavigationFeedback = nil
            buttons.cancel()
            UIApplication.shared.isIdleTimerDisabled = false
        }
        .onChange(of: phase) { _, next in
            UIApplication.shared.isIdleTimerDisabled = next == .active
            if next == .background { buttons.cancel() }
        }
    }

    private func connectButtons() {
        let flow = buttons
        let store = session
        flow.onConfirm = { [weak store] place in
            if let destination = place.wireDestination {
                store?.start(to: destination)
            }
        }
        store.onPhysicalButton = { [weak flow] key in flow?.handle(key.rawValue) }
        store.onNavigationFeedback = { [weak flow] in flow?.receiveNavigationFeedback($0) }
        flow.onDiagnostic = { [weak store] stage in store?.log.write("portable_stage", stage) }
        Task { @MainActor [weak store] in
            let ready = await GrokDestinationInterpreter.shared.prepareCredentials()
            store?.log.write("grok_configuration", ready ? "ready" : "local")
        }
        UIApplication.shared.isIdleTimerDisabled = phase == .active
    }
}

@MainActor
private struct PortableDestinationPanel: View {
    private struct PendingDestination {
        let destination: Destination
        let displayName: String
        let address: String
    }

    @ObservedObject var session: SessionStore
    @ObservedObject var ble: BLECentral
    @ObservedObject var places: PortablePlaceSearch
    @ObservedObject var speech: PortableSpeech
    let buttonStatus: String
    @State private var query = ""
    @State private var pending: PendingDestination?
    @State private var confirming = false

    var body: some View {
        Section("目的地") {
            Text("K2 录音 · K3 下一项 · K1 确认")
                .font(.footnote)
            if !buttonStatus.isEmpty {
                Text(buttonStatus).accessibilityAddTraits(.updatesFrequently)
            }
            TextField("输入目的地", text: $query)
                .submitLabel(.search)
                .onSubmit(search)
            Button("准备语音权限", action: speech.preparePermissions)
                .disabled(speech.listening || speech.working || session.active)
            Button(speech.listening ? "结束录音" : "说出目的地") {
                speech.toggle { text in
                    query = text
                    search()
                }
            }
            .disabled((speech.working && !speech.listening) || session.active)
            .accessibilityHint("说出地名后，再次轻点以结束录音")
            if !speech.status.isEmpty {
                Text(speech.status).accessibilityAddTraits(.updatesFrequently)
            }
            Button("搜索地点", action: search)
                .disabled(query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || speech.listening || session.active)
            if !places.status.isEmpty {
                Text(places.status).accessibilityAddTraits(.updatesFrequently)
            }
            ForEach(places.candidates) { place in
                Button {
                    guard let destination = place.wireDestination else { return }
                    propose(destination, displayName: place.name, address: place.address)
                } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(place.name)
                        if !place.address.isEmpty {
                            Text(place.address).font(.subheadline).foregroundStyle(.secondary)
                        }
                        if let distance = place.distanceMeters, distance.isFinite, distance >= 0 {
                            Text(distance < 1000 ? "直线距离约 \(Int(distance.rounded())) 米"
                                 : "直线距离约 \(String(format: "%.1f", distance / 1000)) 公里")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
                .disabled(session.active || speech.listening)
                .accessibilityHint("核对地点后确认开始导航")
            }
            DisclosureGroup("使用坐标") {
                TextField("目的地名称", text: $session.destinationName)
                TextField("经度", text: $session.longitude)
                    .keyboardType(.decimalPad)
                    .accessibilityLabel("目的地 WGS84 经度")
                TextField("纬度", text: $session.latitude)
                    .keyboardType(.decimalPad)
                    .accessibilityLabel("目的地 WGS84 纬度")
                Button("确认坐标") {
                    guard let destination = session.destination else { return }
                    propose(destination, displayName: destination.name, address: "")
                }
                .disabled(session.destination == nil || session.active)
            }
        }
        .confirmationDialog("确认导航目的地", isPresented: $confirming, titleVisibility: .visible, presenting: pending) { target in
            Button("开始导航") { session.start(to: target.destination) }
                .disabled(!ble.ready || !session.bound || session.active)
        } message: { target in
            Text([target.displayName, target.address, ble.ready && session.bound ? "" : "请先连接已绑定的导航设备"]
                .filter { !$0.isEmpty }.joined(separator: "\n"))
        }
    }

    private func search() {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !speech.listening, !session.active else { return }
        places.search(query)
    }

    private func propose(_ destination: Destination, displayName: String, address: String) {
        pending = PendingDestination(destination: destination, displayName: displayName, address: address)
        confirming = true
    }
}

private struct MacLocationPanel: View {
    @ObservedObject var sharing: MacLocationSharing

    var body: some View {
        Section("向 Mac 共享定位") {
            if let host = sharing.pairedHost {
                LabeledContent("Mac 地址", value: host)
            }
            Text(sharing.status).accessibilityAddTraits(.updatesFrequently)
            if sharing.sharing {
                Button("停止共享", role: .destructive, action: sharing.stop)
            } else {
                SecureField("Mac 配对链接", text: $sharing.pairingLink)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("开始共享", action: sharing.start)
                    .disabled(sharing.pairingLink.isEmpty && sharing.pairedHost == nil)
            }
            Text("保持此页在前台，Mac 会用 iPhone 的位置计算候选距离。")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }
}

private struct DevicePanel: View {
    @ObservedObject var ble: BLECentral
    @ObservedObject var location: LocationService
    let connect: (NavigationPeripheral) -> Void

    var body: some View {
        Section("设备与定位") {
            LabeledContent("蓝牙", value: ble.connection)
            LabeledContent("定位权限", value: location.authorization)
            LabeledContent("精确定位", value: location.precise ? "已启用" : "请开启精确位置")
            Button("更新定位授权", action: location.authorize)
                .accessibilityLabel("更新定位与精确位置授权")
            Button("搜索导航设备", action: ble.scan)
                .accessibilityLabel("搜索附近导航设备")
            if ble.boundPeripheralID == nil {
                Text("首次绑定：将导航设备通过 USB 连接电脑，在电脑终端开启 60 秒配对窗口，再选择下方设备。核对 iPhone 系统弹窗与 USB 终端的六位数字，一致后在两端确认。")
                    .font(.footnote)
            }
            ForEach(ble.devices) { device in
                let label = ble.boundPeripheralID == device.id ? "连接 \(device.name)" : "连接并绑定 \(device.name)"
                Button { connect(device) } label: {
                    VStack(alignment: .leading) {
                        Text(label)
                        Text("信号 \(device.rssi) dBm").font(.caption).foregroundStyle(.secondary)
                    }
                }
                .accessibilityLabel(label)
            }
        }
    }
}
