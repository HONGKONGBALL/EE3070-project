import Foundation

public struct BLELinkIdentity: Equatable {
    public let peripheralID: UUID
    public let generation: UInt64

    public init(peripheralID: UUID, generation: UInt64) {
        self.peripheralID = peripheralID
        self.generation = generation
    }
}

public struct BindHandshake {
    private struct Pending {
        let command: CommandFrame
        let link: BLELinkIdentity
        var attempts = 0
        var lastAttemptAtMS: UInt64?
    }

    private var pending: Pending?
    private let maximumAttempts = 3
    private let retryIntervalMS: UInt64 = 2000

    public init() {}

    public var commandID: String? { pending?.command.command_id }

    public mutating func begin(command: CommandFrame, link: BLELinkIdentity) {
        pending = Pending(command: command, link: link)
    }

    public mutating func command(for message: DeviceMessage, from link: BLELinkIdentity,
                                 at nowMS: UInt64) -> CommandFrame? {
        guard message.type == "status", message.ble == "CONNECTED",
              var context = pending, context.link == link,
              context.attempts < maximumAttempts else { return nil }
        if let previous = context.lastAttemptAtMS {
            guard nowMS >= previous, nowMS - previous >= retryIntervalMS else { return nil }
        }
        context.attempts += 1
        context.lastAttemptAtMS = nowMS
        pending = context
        return context.command
    }

    public mutating func acknowledge(_ message: DeviceMessage, from link: BLELinkIdentity) -> UUID? {
        guard let context = pending, context.link == link, context.attempts > 0,
              message.type == "ack", message.command_id == context.command.command_id,
              message.accepted == true, message.reason == "BOUND" else { return nil }
        pending = nil
        return context.link.peripheralID
    }

    public func isExhausted(at nowMS: UInt64) -> Bool {
        guard let context = pending, context.attempts == maximumAttempts,
              let previous = context.lastAttemptAtMS, nowMS >= previous else { return false }
        return nowMS - previous >= retryIntervalMS
    }

    public mutating func reset() { pending = nil }
}
