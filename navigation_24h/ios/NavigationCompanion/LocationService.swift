import CoreLocation
import Combine
import UIKit

@MainActor
final class LocationService: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    @Published private(set) var authorization = "等待定位授权"
    @Published private(set) var precise = false
    @Published private(set) var latest: FixSample?
    @Published private(set) var active = false
    var onSample: ((FixSample) -> Void)?
    var onDiagnostic: ((String) -> Void)?
    private let manager = CLLocationManager()
    private var pendingStart = false
    private var sourceTracker = SourceMeasurementTracker()
    private var sequence = UInt64(UserDefaults.standard.integer(forKey: "fixSequence"))

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyBest
        manager.distanceFilter = 1
        manager.activityType = .otherNavigation
        manager.pausesLocationUpdatesAutomatically = false
        manager.showsBackgroundLocationIndicator = true
    }

    func authorize() {
        guard UIApplication.shared.applicationState == .active else { return }
        manager.requestWhenInUseAuthorization()
        if manager.accuracyAuthorization == .reducedAccuracy {
            manager.requestTemporaryFullAccuracyAuthorization(withPurposeKey: "NavigationSession")
        }
    }

    func start() {
        guard UIApplication.shared.applicationState == .active else { return }
        pendingStart = true
        authorize()
        startWhenAuthorized()
    }

    func stop() {
        pendingStart = false
        manager.stopUpdatingLocation()
        manager.allowsBackgroundLocationUpdates = false
        active = false
        latest = nil
    }

    private func startWhenAuthorized() {
        guard pendingStart, UIApplication.shared.applicationState == .active else { return }
        switch manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse:
            manager.allowsBackgroundLocationUpdates = true
            manager.startUpdatingLocation()
            active = true
        case .denied, .restricted, .notDetermined: break
        @unknown default: break
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        precise = manager.accuracyAuthorization == .fullAccuracy
        switch manager.authorizationStatus {
        case .authorizedAlways: authorization = "始终允许定位"
        case .authorizedWhenInUse: authorization = "使用期间允许定位"
        case .denied: authorization = "请在设置中允许定位"
        case .restricted: authorization = "定位权限受系统管理"
        case .notDetermined: authorization = "等待定位授权"
        @unknown default: authorization = "定位状态已更新"
        }
        if !precise { authorization += " · 近似位置" }
        onDiagnostic?(authorization)
        startWhenAuthorized()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        let receivedAt = NavigationClock.milliseconds
        let wallNow = Date()
        for location in locations {
            let age = wallNow.timeIntervalSince(location.timestamp) * 1000
            guard location.horizontalAccuracy >= 0,
                  CLLocationCoordinate2DIsValid(location.coordinate), age.isFinite, age >= -1000
            else { continue }
            let timestampMS = Int64(location.timestamp.timeIntervalSince1970 * 1000)
            guard sourceTracker.accept(sourceTimestampMS: timestampMS,
                                       wallNowMS: Int64(wallNow.timeIntervalSince1970 * 1000),
                                       receivedAtMS: receivedAt) else { continue }
            sequence += 1
            UserDefaults.standard.set(sequence, forKey: "fixSequence")
            let trustworthyCourse = location.speed >= 0.5 && location.course >= 0 &&
                location.courseAccuracy >= 0 && location.courseAccuracy <= 45
            let sample = FixSample(sequence: sequence,
                                   sourceTimestampMS: timestampMS,
                                   ageAtReceiptMS: UInt64(max(0, age)), receivedAtMS: receivedAt,
                                   lng: location.coordinate.longitude, lat: location.coordinate.latitude,
                                   accuracyM: location.horizontalAccuracy,
                                   speedMPS: location.speed >= 0 ? location.speed : nil,
                                   courseDeg: trustworthyCourse ? location.course : nil)
            latest = sample
            onSample?(sample)
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        onDiagnostic?("LOCATION_\((error as NSError).code)")
    }
}
