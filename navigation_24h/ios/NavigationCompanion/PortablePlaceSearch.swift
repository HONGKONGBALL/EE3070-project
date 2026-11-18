import Combine
import CoreLocation
import MapKit
import UIKit

struct PortablePlace: Identifiable, Sendable {
    let id: String
    let name: String
    let address: String
    let longitude: Double
    let latitude: Double
    var distanceMeters: Double? = nil

    var wireDestination: Destination? {
        var wireName = ""
        for character in name {
            let next = wireName + String(character)
            guard next.utf8.count <= 96 else { break }
            wireName = next
        }
        return Destination(name: wireName, lng: longitude, lat: latitude)
    }
}

private enum NearbyBrand: Sendable {
    case fitness, mcdonalds

    init?(_ keywords: String) {
        let compact = PortablePlaceQuery.compact(keywords)
        if compact.contains("247fitness") {
            self = .fitness
        } else if compact.contains("麦当劳") || compact.contains("mcdonalds") {
            self = .mcdonalds
        } else {
            return nil
        }
    }

    func matches(_ name: String) -> Bool {
        let compact = PortablePlaceQuery.compact(name)
        switch self {
        case .fitness: return compact.hasPrefix("247fitness")
        case .mcdonalds: return compact.hasPrefix("麦当劳") || compact.hasPrefix("mcdonalds")
        }
    }
}

private enum MainlandMapCoordinates {
    private static let axis = 6_378_245.0
    private static let eccentricity = 0.00669342162296594323

    static func wgs84(
        _ point: CLLocationCoordinate2D, countryCode: String?, locality: String, address: String
    ) -> CLLocationCoordinate2D {
        let labels = [locality, address].map(PortablePlaceQuery.compact).filter { !$0.isEmpty }
        guard countryCode?.uppercased() == "CN", !labels.isEmpty, !labels.contains(where: isSpecialRegion),
              (73.66..<135.05).contains(point.longitude), (3.86..<53.55).contains(point.latitude)
        else { return point }
        var estimate = point
        for _ in 0..<12 {
            let shifted = providerCoordinate(estimate)
            let longitudeError = shifted.longitude - point.longitude
            let latitudeError = shifted.latitude - point.latitude
            estimate.longitude -= longitudeError
            estimate.latitude -= latitudeError
            if abs(longitudeError) < 1e-11, abs(latitudeError) < 1e-11 { break }
        }
        return estimate
    }

    private static func isSpecialRegion(_ label: String) -> Bool {
        let markers = ["香港", "澳门", "澳門", "台湾", "台灣", "臺灣",
                       "hongkong", "macau", "macao", "taiwan"]
        let countryPrefixes = ["中国", "中國", "china"]
        let prefix = countryPrefixes.first(where: label.hasPrefix)
        let geographicLabel = prefix.map { String(label.dropFirst($0.count)) } ?? label
        return markers.contains { marker in
            geographicLabel.hasPrefix(marker) || label.hasSuffix(marker)
                || countryPrefixes.contains { label.hasSuffix(marker + $0) }
        }
    }

    // Same transform as firmware/components/coordinates/gcj02.cpp.
    // Adapted from wandergis/coordtransform, Copyright (c) 2015 记忆的残骸, MIT.
    // License and source revision are recorded in firmware/components/coordinates/third_party/.
    private static func providerCoordinate(_ point: CLLocationCoordinate2D) -> CLLocationCoordinate2D {
        let x = point.longitude - 105
        let y = point.latitude - 35
        var latitudeDelta = -100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*sqrt(abs(x))
        let highFrequency = (20*sin(6*x*Double.pi) + 20*sin(2*x*Double.pi))*2/3
        latitudeDelta += highFrequency
        latitudeDelta += (20*sin(y*Double.pi) + 40*sin(y/3*Double.pi))*2/3
        latitudeDelta += (160*sin(y/12*Double.pi) + 320*sin(y*Double.pi/30))*2/3
        var longitudeDelta = 300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*sqrt(abs(x))
        longitudeDelta += highFrequency
        longitudeDelta += (20*sin(x*Double.pi) + 40*sin(x/3*Double.pi))*2/3
        longitudeDelta += (150*sin(x/12*Double.pi) + 300*sin(x/30*Double.pi))*2/3
        let radians = point.latitude / 180 * Double.pi
        let sine = sin(radians)
        let magic = 1 - eccentricity * sine * sine
        let root = sqrt(magic)
        let latitude = latitudeDelta * 180 / (axis * (1 - eccentricity) / (magic * root) * Double.pi)
        let longitude = longitudeDelta * 180 / (axis / root * cos(radians) * Double.pi)
        return CLLocationCoordinate2D(latitude: point.latitude + latitude, longitude: point.longitude + longitude)
    }
}

private struct PortablePlaceQuery: Sendable {
    let keywords: String
    let nearby: Bool
    let brand: NearbyBrand?

    init?(_ text: String, transcript: String?, interpretedNearby: Bool) {
        let candidate = Self.parse(text)
        let spoken = transcript.map(Self.parse) ?? candidate
        let scopedBrand = NearbyBrand(spoken.keywords) != nil && !spoken.bareBrand && !spoken.saysNearby
        let keywords = scopedBrand || spoken.bareBrand ? spoken.keywords : candidate.keywords
        guard (2...80).contains(keywords.count),
              !keywords.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
        else { return nil }
        self.keywords = keywords
        brand = NearbyBrand(keywords)
        nearby = !scopedBrand && (spoken.saysNearby || spoken.bareBrand
                                || candidate.saysNearby || candidate.bareBrand || interpretedNearby)
    }

    private static func parse(_ text: String) -> (keywords: String, saysNearby: Bool, bareBrand: Bool) {
        let phrase = text.precomposedStringWithCompatibilityMapping
            .trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: "，。！？,!?")))
            .replacingOccurrences(of: #"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])"#,
                                  with: "", options: .regularExpression)
        let destination = phrase.replacingOccurrences(
            of: #"^(?:请|請)?(?:我想要去|我想去|带我去|帶我去|我要去|导航到|導航到|(?:帮|幫)我(?:找一下|找找|找|查找)|查找|找一个|找一個|找|去|take me to\b|go to\b)\s*"#,
            with: "", options: [.regularExpression, .caseInsensitive])
        let unwrapped = destination.replacingOccurrences(
            of: #"^(?:(?:在)?(?:我)?附近的?(?:最近的?)?|(?:[离離]我)?最近的?|(?:the\s+)?(?:nearest|closest|nearby)\s+)\s*|\s+near me$"#,
            with: "", options: [.regularExpression, .caseInsensitive])
        let canonical = unwrapped.replacingOccurrences(
            of: #"(?<![0-9])(?:247|24\s*[/ ]\s*7|二四七|二十四七)\s*(?:健身[房室]|fitness)"#,
            with: "24/7 Fitness", options: [.regularExpression, .caseInsensitive])
            .replacingOccurrences(of: "麥當勞", with: "麦当劳")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let compact = Self.compact(canonical)
        let bareBrand = ["247fitness", "麦当劳", "麦当劳餐厅", "麦当劳餐廳", "mcdonalds"].contains(compact)
        return (canonical, unwrapped != destination, bareBrand)
    }

    static func compact(_ text: String) -> String {
        text.precomposedStringWithCompatibilityMapping.replacingOccurrences(of: "麥當勞", with: "麦当劳")
            .lowercased().filter { $0.isLetter || $0.isNumber }
    }
}

@MainActor
final class PortablePlaceSearch: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    @Published private(set) var candidates: [PortablePlace] = []
    @Published private(set) var status = ""
    @Published private(set) var searching = false
    @Published private(set) var expandedSearch = false

    private let manager = CLLocationManager()
    private var awaitingFix: PortablePlaceQuery?
    private var fixTimeout: Task<Void, Never>?
    private var lookup: Task<Void, Never>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
    }

    func search(_ text: String, transcript: String? = nil, interpretedNearby: Bool = false) {
        cancel()
        candidates = []
        expandedSearch = false
        guard let query = PortablePlaceQuery(text, transcript: transcript, interpretedNearby: interpretedNearby) else {
            status = "请输入地点名称"
            return
        }
        searching = true
        guard query.nearby else {
            find(query, origin: nil)
            return
        }
        guard UIApplication.shared.applicationState == .active else {
            searching = false
            status = "请在手机上打开步行导航，再搜索附近地点"
            return
        }
        awaitingFix = query
        status = "正在取得 iPhone 位置"
        if manager.authorizationStatus == .notDetermined {
            manager.requestWhenInUseAuthorization()
        } else {
            requestAuthorizedFix()
        }
    }

    func cancel() {
        awaitingFix = nil
        fixTimeout?.cancel()
        fixTimeout = nil
        lookup?.cancel()
        lookup = nil
        manager.stopUpdatingLocation()
        searching = false
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        requestAuthorizedFix()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard awaitingFix != nil, let fix = locations.reversed().first(where: Self.fresh) else { return }
        finishFix(fix)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if let error = error as? CLError, error.code == .locationUnknown { return }
        finishFix(nil)
    }

    private func requestAuthorizedFix() {
        guard awaitingFix != nil, fixTimeout == nil else { return }
        switch manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse:
            guard UIApplication.shared.applicationState != .background else { finishFix(nil); return }
            if let location = manager.location, Self.fresh(location) {
                finishFix(location)
                return
            }
            fixTimeout = Task { [weak self] in
                try? await Task.sleep(for: .seconds(8))
                if !Task.isCancelled { self?.finishFix(nil) }
            }
            manager.startUpdatingLocation()
        case .notDetermined: break
        default: finishFix(nil)
        }
    }

    private func finishFix(_ location: CLLocation?) {
        guard let query = awaitingFix else { return }
        awaitingFix = nil
        fixTimeout?.cancel()
        fixTimeout = nil
        manager.stopUpdatingLocation()
        guard let location else {
            searching = false
            status = "尚未取得手机定位。请核对精确位置权限，再搜索附近地点"
            return
        }
        find(query, origin: location)
    }

    private static func fresh(_ location: CLLocation) -> Bool {
        let age = Date().timeIntervalSince(location.timestamp)
        return CLLocationCoordinate2DIsValid(location.coordinate)
            && (0...100).contains(location.horizontalAccuracy) && (-1...10).contains(age)
    }

    private func find(_ query: PortablePlaceQuery, origin: CLLocation?) {
        status = "正在搜索地点"
        lookup = Task { [weak self] in
            let matches: [PortablePlace]?
            let expanded: Bool
            if let origin {
                (matches, expanded) = await Self.nearbyMatches(query, origin: origin)
            } else {
                matches = await Self.mapSearch(query, regions: [nil])
                expanded = false
            }
            guard !Task.isCancelled, let self else { return }
            searching = false
            guard let matches else {
                status = "地图暂时不可用，请稍后重试"
                return
            }
            candidates = Array(matches.prefix(5))
            expandedSearch = expanded
            if candidates.isEmpty {
                status = "可用更具体的地点名再查一次"
            } else {
                status = origin == nil ? "候选按地图相关度排列" :
                    (expanded ? "已扩大搜索范围，候选按直线距离排列" : "候选按直线距离排列")
            }
        }
    }

    private static func nearbyMatches(
        _ query: PortablePlaceQuery, origin: CLLocation
    ) async -> ([PortablePlace]?, Bool) {
        let close = await mapSearch(query, regions: [localRegion(origin.coordinate)])
            .map { ranked($0, from: origin) }
        if (close?.first?.distanceMeters ?? .infinity) <= 1_000 || Task.isCancelled { return (close, false) }
        let wider = await mapSearch(query, regions: nearbyRegions(origin.coordinate))
        let combined = unique((close ?? []) + (wider ?? []))
        if !combined.isEmpty || Task.isCancelled { return (ranked(combined, from: origin), true) }
        let unbounded = await mapSearch(query, regions: [nil])
        return ((unbounded ?? wider ?? close).map { ranked($0, from: origin) }, true)
    }

    private static func ranked(_ matches: [PortablePlace], from origin: CLLocation) -> [PortablePlace] {
        matches.map { place in
            var place = place
            place.distanceMeters = origin.distance(from: CLLocation(latitude: place.latitude, longitude: place.longitude))
            return place
        }.sorted { ($0.distanceMeters ?? .infinity) < ($1.distanceMeters ?? .infinity) }
    }

    private static func localRegion(_ point: CLLocationCoordinate2D) -> MKCoordinateRegion {
        // Snap the provider viewport to a shared grid; keep the precise fix for local ranking.
        let latitude = (point.latitude * 100).rounded() / 100
        let longitude = (point.longitude * 100).rounded() / 100
        let midpoint = CLLocation(latitude: latitude, longitude: longitude)
        return MKCoordinateRegion(center: midpoint.coordinate, latitudinalMeters: 4_000, longitudinalMeters: 4_000)
    }

    private static func nearbyRegions(_ point: CLLocationCoordinate2D) -> [MKCoordinateRegion?] {
        let lat = (point.latitude * 10).rounded() / 10
        let lng = (point.longitude * 10).rounded() / 10
        let radius = 0.16
        let south = max(-90, lat - radius)
        let north = min(90, lat + radius)
        let row = (north - south) / 3
        let halfWidth = abs(lat) + radius >= 90 ? 180 : min(180, radius / cos(lat * .pi / 180))
        return (0..<3).flatMap { index in
            [-0.5, 0.5].map { column in
                let lon = (lng + column * halfWidth + 540).truncatingRemainder(dividingBy: 360) - 180
                return MKCoordinateRegion(
                    center: CLLocationCoordinate2D(latitude: south + (Double(index) + 0.5) * row, longitude: lon),
                    span: MKCoordinateSpan(latitudeDelta: row, longitudeDelta: halfWidth))
            }
        }
    }

    private static func mapSearch(
        _ query: PortablePlaceQuery, regions: [MKCoordinateRegion?]
    ) async -> [PortablePlace]? {
        await withTaskGroup(of: (Int, [PortablePlace]?).self) { group in
            for (index, region) in regions.enumerated() {
                group.addTask { @MainActor in
                    let request = MKLocalSearch.Request()
                    request.naturalLanguageQuery = query.keywords
                    request.resultTypes = [.pointOfInterest, .address]
                    if let region {
                        request.region = region
                        if #available(iOS 18.0, *) { request.regionPriority = .required }
                    }
                    do {
                        let operation = MKLocalSearch(request: request)
                        let matches = try await withTaskCancellationHandler {
                            try await operation.start()
                        } onCancel: {
                            Task { @MainActor in operation.cancel() }
                        }
                        return (index, matches.mapItems.compactMap { Self.place($0, query: query) })
                    } catch let error as MKError where error.code == .placemarkNotFound {
                        return (index, [])
                    } catch {
                        return (index, nil)
                    }
                }
            }
            var batches: [[PortablePlace]?] = Array(repeating: nil, count: regions.count)
            for await (index, results) in group {
                batches[index] = results
            }
            let successful = batches.compactMap { $0 }
            return successful.isEmpty ? nil : unique(Array(successful.joined()))
        }
    }

    private static func unique(_ matches: [PortablePlace]) -> [PortablePlace] {
        var seen = Set<String>()
        return matches.filter { seen.insert($0.id).inserted }
    }

    private static func place(_ item: MKMapItem, query: PortablePlaceQuery) -> PortablePlace? {
        guard let name = item.name, !name.isEmpty,
              query.brand?.matches(name) != false
        else { return nil }
        let point: CLLocationCoordinate2D
        let address: String
        let countryCode: String?
        let locality: String
        if #available(iOS 26.0, *) {
            point = item.location.coordinate
            address = item.address?.fullAddress ?? ""
            countryCode = item.addressRepresentations?.region?.identifier
            locality = item.addressRepresentations?.cityName ?? ""
        } else {
            point = item.placemark.coordinate
            address = item.placemark.title ?? ""
            countryCode = item.placemark.isoCountryCode
            locality = item.placemark.locality ?? ""
        }
        guard CLLocationCoordinate2DIsValid(point) else { return nil }
        let coordinate = MainlandMapCoordinates.wgs84(point, countryCode: countryCode,
                                                       locality: locality, address: address)
        let fallbackID = "\(name)|\(point.latitude)|\(point.longitude)"
        let id: String
        if #available(iOS 18.0, *) { id = item.identifier?.rawValue ?? fallbackID }
        else { id = fallbackID }
        return PortablePlace(id: id, name: name, address: address,
                             longitude: coordinate.longitude, latitude: coordinate.latitude)
    }
}
