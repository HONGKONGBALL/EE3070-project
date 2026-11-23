import Foundation

public enum FrameError: Error { case invalidFrame, capacity, writeLength, sequence, expired }

public enum FrameCodec {
    public static func fragments(_ payload: Data, messageID: UInt16, maximumWriteLength: Int) throws -> [Data] {
        guard !payload.isEmpty, payload.count <= 512 else { throw FrameError.capacity }
        guard maximumWriteLength > 10 else { throw FrameError.writeLength }
        let stride = maximumWriteLength - 10
        let total = UInt16(payload.count)
        var packets: [Data] = []
        for offset in Swift.stride(from: 0, to: payload.count, by: stride) {
            let index = UInt16(offset)
            var packet = Data([0x4e, 0x31, 1, 0,
                               UInt8(messageID & 0xff), UInt8(messageID >> 8),
                               UInt8(total & 0xff), UInt8(total >> 8),
                               UInt8(index & 0xff), UInt8(index >> 8)])
            packet.append(payload.subdata(in: offset..<min(offset + stride, payload.count)))
            packets.append(packet)
        }
        return packets
    }
}

public struct FrameReassembler {
    private var identifier: UInt16?
    private var total = 0
    private var startedAt: UInt64 = 0
    private var buffer = Data()
    public init() {}
    public mutating func receive(_ packet: Data, at now: UInt64) throws -> Data? {
        do {
            let bytes = Array(packet)
            guard bytes.count > 10, bytes[0...3] == [0x4e, 0x31, 1, 0] else {
                throw FrameError.invalidFrame
            }
            let messageID = UInt16(bytes[4]) | UInt16(bytes[5]) << 8
            let length = Int(bytes[6]) | Int(bytes[7]) << 8
            let offset = Int(bytes[8]) | Int(bytes[9]) << 8
            guard (1...512).contains(length), offset + bytes.count - 10 <= length else {
                throw FrameError.capacity
            }
            if offset == 0 {
                identifier = messageID
                total = length
                startedAt = now
                buffer.removeAll(keepingCapacity: true)
            }
            guard identifier == messageID, total == length, buffer.count == offset else {
                throw FrameError.sequence
            }
            guard now >= startedAt, now - startedAt < 1000 else { throw FrameError.expired }
            buffer.append(contentsOf: bytes.dropFirst(10))
            if buffer.count == total {
                let result = buffer
                self = FrameReassembler()
                return result
            }
            return nil
        } catch {
            self = FrameReassembler()
            throw error
        }
    }
}
