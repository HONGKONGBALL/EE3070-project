import CoreLocation
import CryptoKit
import Foundation
import MapKit

private struct SearchInput: Decodable {
    let query: String
    let nearby: Bool?
    let region: SearchAnchor?
}

private struct SearchAnchor: Decodable {
    let lat: Double
    let lng: Double

    var isPublicGridPoint: Bool {
        (-90...90).contains(lat) && (-180...180).contains(lng)
            && [lat, lng].allSatisfy { abs(10 * $0 - (10 * $0).rounded()) < 0.000_001 }
    }
}

private struct Candidate: Encodable {
    let id: String
    let name: String
    let address: String
    let lng: Double
    let lat: Double
    let coordinate_frame = "WGS84"
}

private struct SearchOutput: Encodable {
    let status = "ok"
    let candidates: [Candidate]
}

private struct SearchError: Encodable {
    let status = "error"
    let code: String
}

private let queryLimit = 80
private let inputLimit = 4_096
private let resultLimit = 5
private let nearbyLimit = 160
private let searchTimeout: TimeInterval = 12
private let latitudeRadius = 0.16

private func emit(_ output: some Encodable, exitCode: Int32 = 0) -> Never {
    if let data = try? JSONEncoder().encode(output) {
        print(String(decoding: data, as: UTF8.self))
        exit(exitCode)
    }
    print(#"{"status":"error","code":"PLACE_RESPONSE"}"#)
    exit(2)
}

private func fail(_ code: String) -> Never {
    emit(SearchError(code: code), exitCode: 2)
}

private func readQuery() throws -> SearchInput? {
    let source = FileHandle.standardInput
    var input = Data()
    while input.count <= inputLimit,
          let chunk = try source.read(upToCount: inputLimit + 1 - input.count),
          !chunk.isEmpty {
        input.append(chunk)
    }
    guard input.count <= inputLimit else { return nil }
    let request = try JSONDecoder().decode(SearchInput.self, from: input)
    let query = request.query.trimmingCharacters(in: .whitespacesAndNewlines)
    guard request.region?.isPublicGridPoint != false, (1...queryLimit).contains(query.count),
          !query.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains) else {
        return nil
    }
    return SearchInput(query: query, nearby: request.nearby, region: request.region)
}

private func oneLine(_ text: String?) -> String {
    text?.split(whereSeparator: \.isWhitespace).joined(separator: " ") ?? ""
}

private func compactName(_ text: String) -> String {
    text.precomposedStringWithCompatibilityMapping.lowercased().filter { $0.isLetter || $0.isNumber }
}

@available(macOS 26.0, *)
private func address(_ item: MKMapItem) -> String {
    let fullAddress = oneLine(item.address?.fullAddress)
    return fullAddress.isEmpty ? oneLine(item.address?.shortAddress) : fullAddress
}

@available(macOS 26.0, *)
private func candidates(_ response: MKLocalSearch.Response, query: String) -> [Candidate] {
    var results: [Candidate] = []
    var seen: Set<String> = []
    let requestsFitnessBrand = compactName(query).contains("247fitness")
    for item in response.mapItems {
        let point = item.location.coordinate
        let name = oneLine(item.name)
        guard CLLocationCoordinate2DIsValid(point), !name.isEmpty else { continue }
        if requestsFitnessBrand && !compactName(name).hasPrefix("247fitness") {
            continue
        }
        let address = address(item)
        let identity = item.identifier?.rawValue ?? [
            name, address, String(point.latitude), String(point.longitude),
        ].joined(separator: "\n")
        let digest = SHA256.hash(data: Data(identity.utf8))
        let id = "apple-" + digest.prefix(16).map { String(format: "%02x", $0) }.joined()
        guard seen.insert(id).inserted else { continue }
        results.append(Candidate(id: id, name: String(name.prefix(256)),
                                 address: String(address.prefix(512)),
                                 lng: point.longitude, lat: point.latitude))
    }
    return results
}

private func region(latitude: ClosedRange<Double>, longitude: ClosedRange<Double>) -> MKCoordinateRegion {
    let longitudeMidpoint = (longitude.lowerBound + longitude.upperBound) / 2
    let center = CLLocationCoordinate2D(
        latitude: (latitude.lowerBound + latitude.upperBound) / 2,
        longitude: (longitudeMidpoint + 540).truncatingRemainder(dividingBy: 360) - 180
    )
    let span = MKCoordinateSpan(
        latitudeDelta: latitude.upperBound - latitude.lowerBound,
        longitudeDelta: longitude.upperBound - longitude.lowerBound
    )
    return MKCoordinateRegion(center: center, span: span)
}

private func searchRegions(around anchor: SearchAnchor, nearby: Bool) -> [MKCoordinateRegion] {
    let south = max(-90, anchor.lat - latitudeRadius)
    let north = min(90, anchor.lat + latitudeRadius)
    let longitudeRadius = abs(anchor.lat) + latitudeRadius >= 90
        ? 180 : min(180, latitudeRadius / cos(anchor.lat * .pi / 180))
    let west = anchor.lng - longitudeRadius
    let east = anchor.lng + longitudeRadius
    if !nearby {
        return [region(latitude: south...north, longitude: west...east)]
    }
    let rowSize = (north - south) / 3
    let rows = [south...(south + rowSize), (south + rowSize)...(north - rowSize), (north - rowSize)...north]
    let columns = [west...anchor.lng, anchor.lng...east]
    return rows.flatMap { row in columns.map { region(latitude: row, longitude: $0) } }
}

@available(macOS 26.0, *)
@MainActor
private final class PlaceSearch {
    private let input: SearchInput
    private let searches: [MKLocalSearch]
    private var batches: [[Candidate]?]

    init(_ input: SearchInput) {
        self.input = input
        let nearby = input.nearby == true
        let regions: [MKCoordinateRegion?] = input.region.map { searchRegions(around: $0, nearby: nearby) } ?? [nil]
        self.searches = regions.map { area in
            let request = MKLocalSearch.Request()
            request.naturalLanguageQuery = input.query
            if let area {
                request.region = area
                if nearby { request.regionPriority = .required }
            }
            request.resultTypes = [.pointOfInterest, .address]
            return MKLocalSearch(request: request)
        }
        self.batches = Array(repeating: nil, count: regions.count)
    }

    func run() -> Never {
        for (index, search) in searches.enumerated() {
            search.start { response, error in
                self.receive(response, error: error, index: index)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + searchTimeout) {
            for search in self.searches { search.cancel() }
            fail("PLACE_TIMEOUT")
        }
        RunLoop.main.run()
        fail("PLACE_UNAVAILABLE")
    }

    private func receive(_ response: MKLocalSearch.Response?, error: Error?, index: Int) {
        if let response {
            batches[index] = candidates(response, query: input.query)
        } else {
            let mapError = error as? MKError
            guard mapError?.code == .placemarkNotFound else {
                fail(mapError?.code == .loadingThrottled ? "PLACE_QUOTA" : "PLACE_UNAVAILABLE")
            }
            batches[index] = []
        }
        let completed = batches.compactMap { $0 }
        guard completed.count == searches.count else { return }
        var seen: Set<String> = []
        let results = completed.joined().filter { seen.insert($0.id).inserted }
        if input.nearby == true {
            guard results.count <= nearbyLimit else { fail("PLACE_CAPACITY") }
            emit(SearchOutput(candidates: results))
        }
        emit(SearchOutput(candidates: Array(results.prefix(resultLimit))))
    }
}

guard #available(macOS 26.0, *) else { fail("PLACE_CONFIGURATION") }
guard let query = try? readQuery() else { fail("PLACE_INPUT") }
MainActor.assumeIsolated {
    PlaceSearch(query).run()
}
