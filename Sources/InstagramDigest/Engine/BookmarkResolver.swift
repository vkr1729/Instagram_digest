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
    private let configuredEndpoint: URL?
    private let configuredApiKey: String?

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

        self.configuredEndpoint = endpoint
        self.configuredApiKey = apiKey
    }

    /// Validates an endpoint URL string: enforces HTTPS, valid host, and strips trailing slash (AUDIT-063)
    public static func validateEndpoint(_ urlString: String?) -> URL? {
        guard let raw = urlString?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty else { return nil }
        var clean = raw
        if clean.hasSuffix("/") {
            clean = String(clean.dropLast())
        }
        guard let u = URL(string: clean),
              u.scheme?.lowercased() == "https",
              let host = u.host, !host.isEmpty else {
            return nil
        }
        return u
    }

    public func effectiveEndpoint() -> URL {
        if let configured = configuredEndpoint {
            return configured
        }
        if let customStr = UserDefaults.standard.string(forKey: "cobalt_api_url"),
           let valid = Self.validateEndpoint(customStr) {
            return valid
        }
        return Self.defaultEndpoint
    }

    public func effectiveApiKey() -> String? {
        if let configured = configuredApiKey {
            return configured
        }
        let key = UserDefaults.standard.string(forKey: "cobalt_api_key")
            ?? UserDefaults.standard.string(forKey: "cobalt_api_token")
        let trimmed = key?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (trimmed?.isEmpty ?? true) ? nil : trimmed
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

        let endpoint = effectiveEndpoint()
        let apiKey = effectiveApiKey()

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
                // AUDIT-064: Case-insensitive HTTPS scheme and non-nil host check
                if let streamStr = json["url"] as? String,
                   let streamURL = URL(string: streamStr),
                   streamURL.scheme?.lowercased() == "https",
                   let host = streamURL.host, !host.isEmpty {
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
            // AUDIT-026: Differentiate genuine network errors from programmer errors and cancellation
            switch urlError.code {
            case .notConnectedToInternet, .networkConnectionLost, .timedOut,
                 .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed,
                 .secureConnectionFailed, .serverCertificateUntrusted:
                return .offline
            case .cancelled, .userCancelledAuthentication:
                return .failed("Task cancelled")
            default:
                return .failed(urlError.localizedDescription)
            }
        } catch {
            return .failed(error.localizedDescription)
        }
    }
}
