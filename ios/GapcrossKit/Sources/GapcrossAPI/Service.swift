import Foundation
import OpenAPIURLSession

extension Client {
    /// The service the app talks to. The simulator reaches the Mac's Docker service as localhost.
    public static func service(at url: URL = URL(string: "http://localhost:8000")!) -> Client {
        Client(serverURL: url, transport: URLSessionTransport())
    }
}
