import Testing
@testable import GapcrossCore

@Suite struct TextTests {
    @Test func rule14NoAppStringSaysSafe() {
        #expect(!AppText.all.isEmpty)
        for text in AppText.all {
            #expect(!text.lowercased().contains("safe"), "\(text)")
        }
    }
}
