import Testing
@testable import GapcrossCore

@Suite struct UnitsTests {
    @Test func feetUnderAboutATenthOfAMile() {
        #expect(Distance.display(30) == "100 ft")    // 98 ft, rounded to 50 ft
        #expect(Distance.display(91.44) == "300 ft")
        #expect(Distance.display(150) == "500 ft")   // 492 ft
        #expect(Distance.spoken(91.44) == "300 feet")
    }

    @Test func milesFromATenthOfAMile() {
        #expect(Distance.display(161) == "0.1 mi")
        #expect(Distance.display(643.7) == "0.4 mi")
        #expect(Distance.display(6005) == "3.7 mi")
        #expect(Distance.spoken(1609.344) == "1 mile")
        #expect(Distance.spoken(643.7) == "0.4 miles")
    }

    @Test func driveTimeIsWholeMinutes() {
        #expect(DriveTime.display(754) == "13 min")
        #expect(DriveTime.display(20) == "1 min")
        #expect(DriveTime.display(3720) == "1 hr 2 min")
    }
}
