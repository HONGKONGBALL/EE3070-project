import Combine
import CoreLocation
import CryptoKit
import Foundation
import Security

private struct MacPairing: Equatable {
    let endpoint: URL
    let host: String
    let fingerprint: String
    let token: String

    init?(_ url: URL) {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "ee3070-cane", components.host == "mac-location",
              components.path.isEmpty, components.user == nil, components.password == nil,
              components.port == nil, components.fragment == nil,
              let items = components.queryItems, items.count == 4,
              let host = Self.value(named: "host", in: items), Self.isLocalIPv4(host),
              let portValue = Self.value(named: "port", in: items),
              let port = Int(portValue), (1024...65535).contains(port), String(port) == portValue,
              let suppliedPin = Self.value(named: "fingerprint", in: items),
              suppliedPin.utf8.count == 64, suppliedPin.utf8.allSatisfy(Self.isHexDigit),
              let token = Self.value(named: "token", in: items), (32...512).contains(token.utf8.count),
              token.utf8.allSatisfy(Self.isTokenCharacter)
        else { return nil }
        var endpoint = URLComponents()
        endpoint.scheme = "https"
        endpoint.host = host
        endpoint.port = port
        endpoint.path = "/api/phone/location"
        guard let url = endpoint.url else { return nil }
        self.endpoint = url
        self.host = host
        self.fingerprint = suppliedPin.lowercased()
        self.token = token
    }

    private static func value(named name: String, in items: [URLQueryItem]) -> String? {
        let matches = items.filter { $0.name == name }
        return matches.count == 1 ? matches[0].value : nil
    }

    private static func isLocalIPv4(_ host: String) -> Bool {
        let pieces = host.split(separator: ".", omittingEmptySubsequences: false)
        let octets = pieces.compactMap { UInt8($0) }
        guard pieces.count == 4, octets.count == 4,
              octets.map(String.init).joined(separator: ".") == host,
              (1...254).contains(octets[3])
        else { return false }
        return octets[0] == 10 || (octets[0] == 172 && (16...31).contains(octets[1])) ||
            (octets[0] == 192 && octets[1] == 168)
    }

    private static func isHexDigit(_ byte: UInt8) -> Bool {
        (48...57).contains(byte) || (65...70).contains(byte) || (97...102).contains(byte)
    }

    private static func isTokenCharacter(_ byte: UInt8) -> Bool {
        (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte) ||
            byte == 45 || byte == 95
    }
}

private final class MacLocationTrust: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let pairing: MacPairing

    init(_ pairing: MacPairing) {
        self.pairing = pairing
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping @Sendable (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        let space = challenge.protectionSpace
        guard space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              space.host == pairing.host, space.port == pairing.endpoint.port,
              let trust = space.serverTrust,
              let leaf = (SecTrustCopyCertificateChain(trust) as? [SecCertificate])?.first
        else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let digest = SHA256.hash(data: SecCertificateCopyData(leaf) as Data)
            .map { String(format: "%02x", $0) }.joined()
        guard digest == pairing.fingerprint,
              SecTrustSetAnchorCertificates(trust, [leaf] as CFArray) == errSecSuccess,
              SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess,
              SecTrustEvaluateWithError(trust, nil)
        else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    func urlSession(
        _ session: URLSession, task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

@MainActor
final class MacLocationSharing: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    @Published var pairingLink = ""
    @Published private(set) var status = "等待 Mac 配对"
    @Published private(set) var sharing = false
    @Published private(set) var pairedHost: String?

    private struct Coordinates: Encodable {
        let lng: Double
        let lat: Double
        let accuracy: Double
        let token: String
    }

    private lazy var manager: CLLocationManager = {
        let manager = CLLocationManager()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        return manager
    }()
    private var pairing: MacPairing?
    private var session: URLSession?
    private var requestTask: Task<Void, Never>?
    private var timer: Timer?
    private var foreground = true
    private var awaitingFix = false

    func stage(_ url: URL) {
        guard url.scheme == "ee3070-cane", url.host == "mac-location" else { return }
        guard let received = MacPairing(url) else {
            status = "请重新从 Mac 打开配对链接"
            return
        }
        if sharing { stop() }
        pairing = received
        pairedHost = received.host
        pairingLink = ""
        status = "配对信息已收到"
    }

    func start() {
        guard !sharing, foreground else { return }
        let input = pairingLink.trimmingCharacters(in: .whitespacesAndNewlines)
        if !input.isEmpty {
            guard let url = URL(string: input), let received = MacPairing(url) else {
                status = "请粘贴 Mac 页面上的配对链接"
                return
            }
            pairing = received
            pairedHost = received.host
            pairingLink = ""
        }
        guard let pairing else { return }
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 7
        config.timeoutIntervalForResource = 8
        session = URLSession(configuration: config, delegate: MacLocationTrust(pairing), delegateQueue: nil)
        sharing = true
        status = "正在获取 iPhone 位置"
        manager.requestWhenInUseAuthorization()
        beginPolling()
    }

    func stop() {
        sharing = false
        endPolling()
        session?.invalidateAndCancel()
        session = nil
        status = "定位共享已停止"
    }

    func setForeground(_ foreground: Bool) {
        self.foreground = foreground
        guard sharing else { return }
        if foreground {
            status = "正在更新 iPhone 位置"
            beginPolling()
        } else {
            endPolling()
            status = "返回此页继续共享"
        }
    }

    private func beginPolling() {
        guard timer == nil else { return }
        requestFix()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.requestFix() }
        }
    }

    private func endPolling() {
        timer?.invalidate()
        timer = nil
        manager.stopUpdatingLocation()
        awaitingFix = false
        requestTask?.cancel()
        requestTask = nil
    }

    private func requestFix() {
        guard sharing, foreground, !awaitingFix, requestTask == nil else { return }
        switch manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse:
            awaitingFix = true
            manager.requestLocation()
        case .denied, .restricted:
            status = "请在 iPhone 设置中允许步行导航定位"
        case .notDetermined:
            status = "等待 iPhone 定位授权"
        @unknown default:
            status = "等待 iPhone 定位"
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        requestFix()
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        awaitingFix = false
        guard sharing, foreground else { return }
        status = "正在重试定位"
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        awaitingFix = false
        guard sharing, foreground, requestTask == nil, let pairing, let session,
              let fix = locations.reversed().first(where: Self.isFresh) else { return }
        let body = Coordinates(lng: fix.coordinate.longitude, lat: fix.coordinate.latitude,
                               accuracy: fix.horizontalAccuracy, token: pairing.token)
        var request = URLRequest(url: pairing.endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        guard let data = try? JSONEncoder().encode(body) else { return }
        request.httpBody = data
        requestTask = Task { [weak self] in
            let message: String
            do {
                let (_, response) = try await session.data(for: request)
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    message = "已共享 · 精度约 \(Int(fix.horizontalAccuracy.rounded())) 米"
                } else {
                    message = "请从 Mac 重新获取配对链接"
                }
            } catch {
                message = "连接 Mac 失败，正在重试"
            }
            guard !Task.isCancelled, let self else { return }
            self.requestTask = nil
            self.status = message
        }
    }

    private static func isFresh(_ fix: CLLocation) -> Bool {
        let age = Date().timeIntervalSince(fix.timestamp)
        return (-1...8).contains(age) && (0...10000).contains(fix.horizontalAccuracy) &&
            CLLocationCoordinate2DIsValid(fix.coordinate)
    }
}
