import Foundation

/// Possible outcomes when resolving an Instagram reel via an external media resolver API.
public enum ResolverResult: Sendable, Equatable {
    case success(URL)
    case offline
    case rateLimited(retryAfter: TimeInterval?)
    case authOrChallengeRequired
    case unsupportedFormat
    case failed(String)
}

/// Actor responsible for dynamically resolving fresh playable stream URLs for Instagram reels on demand.
public actor BookmarkResolver {
    public static let shared = BookmarkResolver()

    public static let defaultEndpoint = URL(string: "https://api.cobalt.tools/")!

    private let session: URLSession
    private let endpoint: URL
    private let apiKey: String?

    public init(
        session: URLSession? = nil,
        endpoint: URL? = nil,
        apiKey: String? = nil
    ) {
        if let session = session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 15.0
            config.timeoutIntervalForResource = 25.0
            config.waitsForConnectivity = false
            self.session = URLSession(configuration: config)
        }

        if let endpoint = endpoint {
            self.endpoint = endpoint
        } else if let customUrlStr = UserDefaults.standard.string(forKey: "cobalt_api_url"),
                  let customURL = URL(string: customUrlStr),
                  !customUrlStr.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            self.endpoint = customURL
        } else {
            self.endpoint = Self.defaultEndpoint
        }

        if let apiKey = apiKey {
            self.apiKey = apiKey
        } else {
            let key = UserDefaults.standard.string(forKey: "cobalt_api_key")
                ?? UserDefaults.standard.string(forKey: "cobalt_api_token")
            self.apiKey = key
        }
    }

    /// Resolves a canonical reel permalink to a fresh, playable MP4 stream URL.
    /// Retries at most once for transient 429 or 5xx responses.
    public func resolve(url: URL) async -> ResolverResult {
        return await resolveWithRetry(url: url, attempt: 1)
    }

    private func resolveWithRetry(url: URL, attempt: Int) async -> ResolverResult {
        if Task.isCancelled {
            return .failed("Task cancelled")
        }

        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let key = apiKey?.trimmingCharacters(in: .whitespacesAndNewlines), !key.isEmpty {
            let headerVal = (key.hasPrefix("Bearer ") || key.hasPrefix("Api-Key ")) ? key : "Bearer \(key)"
            request.setValue(headerVal, forHTTPHeaderField: "Authorization")
        }

        let body: [String: String] = ["url": url.absoluteString]
        guard let httpBody = try? JSONSerialization.data(withJSONObject: body) else {
            return .failed("Failed to serialize request body")
        }
        request.httpBody = httpBody

        do {
            let (data, response) = try await session.data(for: request)
            if Task.isCancelled {
                return .failed("Task cancelled")
            }

            guard let http = response as? HTTPURLResponse else {
                return .failed("Invalid non-HTTP response")
            }

            if http.statusCode == 429 {
                var retryAfter: TimeInterval? = nil
                if let header = http.value(forHTTPHeaderField: "Retry-After"), let sec = Double(header) {
                    retryAfter = sec
                }
                if attempt == 1 {
                    let wait = min(retryAfter ?? 1.5, 3.0)
                    try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                    if Task.isCancelled {
                        return .failed("Task cancelled")
                    }
                    return await resolveWithRetry(url: url, attempt: 2)
                }
                return .rateLimited(retryAfter: retryAfter)
            }

            if http.statusCode == 400 {
                if let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                   let errObj = json["error"] as? [String: Any],
                   let code = (errObj["code"] as? String)?.lowercased() {
                    if code.contains("auth") || code.contains("jwt") {
                        return .authOrChallengeRequired
                    }
                    return .failed("Resolver error: \(code)")
                }
                let bodyText = (String(data: data, encoding: .utf8) ?? "").lowercased()
                if bodyText.contains("auth") || bodyText.contains("jwt") {
                    return .authOrChallengeRequired
                }
                return .failed("HTTP 400: Bad Request")
            }

            if http.statusCode == 401 || http.statusCode == 403 {
                return .authOrChallengeRequired
            }

            if (500...599).contains(http.statusCode) {
                if attempt == 1 {
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    if Task.isCancelled {
                        return .failed("Task cancelled")
                    }
                    return await resolveWithRetry(url: url, attempt: 2)
                }
                return .failed("Server error (HTTP \(http.statusCode))")
            }

            guard (200...299).contains(http.statusCode) else {
                return .failed("HTTP \(http.statusCode)")
            }

            // AUDIT-035: Check Content-Type header and sniff body for HTML challenge pages
            let contentType = (http.value(forHTTPHeaderField: "Content-Type") ?? "").lowercased()
            if contentType.contains("text/html") {
                return .authOrChallengeRequired
            }
            if let prefix = String(data: data.prefix(256), encoding: .utf8)?.lowercased(),
               prefix.contains("<!doctype") || prefix.contains("<html") {
                return .authOrChallengeRequired
            }

            // Parse response JSON
            guard let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
                return .failed("Malformed JSON response")
            }

            let status = (json["status"] as? String) ?? ""
            if status == "tunnel" || status == "redirect" {
                if let streamStr = json["url"] as? String, let streamURL = URL(string: streamStr), streamURL.scheme == "https" {
                    return .success(streamURL)
                }
                return .failed("Missing or invalid HTTPS stream URL in response")
            } else if status == "local-processing" || status == "picker" {
                return .unsupportedFormat
            } else if status == "error" {
                let errObj = json["error"] as? [String: Any]
                let code = (errObj?["code"] as? String) ?? "unknown_error"
                return .failed("Resolver error: \(code)")
            }

            return .failed("Unexpected resolver status: \(status)")

        } catch let urlError as URLError {
            if attempt == 1 && (urlError.code == .timedOut || urlError.code == .cannotConnectToHost) {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled {
                    return .failed("Task cancelled")
                }
                return await resolveWithRetry(url: url, attempt: 2)
            }
            if urlError.code == .notConnectedToInternet || urlError.code == .networkConnectionLost || urlError.code == .timedOut {
                return .offline
            }
            return .offline
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
