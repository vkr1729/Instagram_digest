import SwiftUI

/// Settings sheet for configuring anti-doomscroll controls and mind guardrails.
public struct SettingsSheetView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    @Binding public var timeQuotaMinutes: Int
    @Binding public var enforcementMode: String
    @Binding public var advanceMode: String
    @Binding public var breatherEnabled: Bool
    @Binding public var isGrayscale: Bool
    @Binding public var hideRanks: Bool
    @Binding public var feedSortOrder: String
    @AppStorage("gh_bookmark_repo") private var ghRepo: String = "vkreddy1729-ops/ig-bookmarks"
    @AppStorage("gh_bookmark_pat") private var ghPAT: String = ""
    @AppStorage("max_cached_bookmark_videos") private var maxCachedVideos: Int = 50

    public var onTapOffline: () -> Void
    public var onTapManifesto: () -> Void

    public init(
        timeQuotaMinutes: Binding<Int>,
        enforcementMode: Binding<String>,
        advanceMode: Binding<String>,
        breatherEnabled: Binding<Bool>,
        isGrayscale: Binding<Bool>,
        hideRanks: Binding<Bool>,
        feedSortOrder: Binding<String>,
        onTapOffline: @escaping () -> Void = {},
        onTapManifesto: @escaping () -> Void = {}
    ) {
        self._timeQuotaMinutes = timeQuotaMinutes
        self._enforcementMode = enforcementMode
        self._advanceMode = advanceMode
        self._breatherEnabled = breatherEnabled
        self._isGrayscale = isGrayscale
        self._hideRanks = hideRanks
        self._feedSortOrder = feedSortOrder
        self.onTapOffline = onTapOffline
        self.onTapManifesto = onTapManifesto
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Daily Time Quota").foregroundColor(.white.opacity(0.6))) {
                    Picker("Daily Watch Limit", selection: $timeQuotaMinutes) {
                        Text("10 minutes").tag(10)
                        Text("20 minutes").tag(20)
                        Text("30 minutes").tag(30)
                        Text("45 minutes").tag(45)
                    }
                    .pickerStyle(.menu)

                    Picker("Enforcement Mode", selection: $enforcementMode) {
                        Text("Gentle Pause").tag("gentle")
                        Text("Hard Lockout").tag("hard")
                    }
                    .pickerStyle(.segmented)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("Playback Friction").foregroundColor(.white.opacity(0.6))) {
                    Picker("Advance Mode", selection: $advanceMode) {
                        Text("Auto (0.5s pause)").tag("auto")
                        Text("Manual (Swipe next)").tag("manual")
                    }
                    .pickerStyle(.segmented)

                    Toggle("3-Second Inter-Reel Breather", isOn: $breatherEnabled)
                        .tint(Color.cyan)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("Visual Stimulation").foregroundColor(.white.opacity(0.6))) {
                    Toggle("Grayscale Detox Mode", isOn: $isGrayscale)
                        .tint(Color.cyan)

                    Toggle("Hide Rank Badges (#01)", isOn: $hideRanks)
                        .tint(Color.cyan)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("Feed Sorting").foregroundColor(.white.opacity(0.6))) {
                    Picker("Sort Order", selection: $feedSortOrder) {
                        Text("Fair-Share Viral").tag("rank")
                        Text("Chronological (Newest)").tag("chrono")
                    }
                    .pickerStyle(.segmented)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("GitHub Bookmark Backup").foregroundColor(.white.opacity(0.6)),
                        footer: Text("Syncs bookmarks to bookmarks.json in your GitHub repository.").foregroundColor(.white.opacity(0.4))) {
                    TextField("Repository (e.g. owner/repo)", text: $ghRepo)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)

                    SecureField("Personal Access Token (PAT)", text: $ghPAT)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("Offline Bookmark Video Storage").foregroundColor(.white.opacity(0.6)),
                        footer: Text("Only the latest N bookmarked videos are cached on disk to prevent storage bloat. Older bookmarks retain their details and thumbnails, but free up video storage.").foregroundColor(.white.opacity(0.4))) {
                    Picker("Keep Offline Videos", selection: $maxCachedVideos) {
                        Text("Latest 25 videos (~250 MB)").tag(25)
                        Text("Latest 50 videos (~500 MB)").tag(50)
                        Text("Latest 100 videos (~1 GB)").tag(100)
                        Text("Keep All Videos").tag(0)
                    }
                    .pickerStyle(.menu)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("Features & Philosophy").foregroundColor(.white.opacity(0.6))) {
                    Button {
                        dismiss()
                        onTapOffline()
                    } label: {
                        HStack {
                            Label("Download All for Offline", systemImage: "arrow.down.circle")
                                .foregroundColor(.white)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.white.opacity(0.4))
                        }
                    }
                    .accessibilityIdentifier("OfflineIconButton")

                    Button {
                        dismiss()
                        onTapManifesto()
                    } label: {
                        HStack {
                            Label("The 10 Traps Manifesto", systemImage: "info.circle")
                                .foregroundColor(.white)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.white.opacity(0.4))
                        }
                    }
                    .accessibilityIdentifier("ManifestoIconButton")
                }
                .listRowBackground(Color.white.opacity(0.08))
            }
            .scrollContentBackground(.hidden)
            .background(Color.black.ignoresSafeArea())
            .navigationTitle("Anti-Doomscroll Controls")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        saveSettings()
                        dismiss()
                    }
                    .foregroundColor(.white)
                    .accessibilityIdentifier("SettingsDoneButton")
                }
            }
        }
    }

    private func saveSettings() {
        UserDefaults.standard.set(timeQuotaMinutes, forKey: "ig_digest_quota_minutes")
        UserDefaults.standard.set(enforcementMode, forKey: "ig_digest_enforcement_mode")
        UserDefaults.standard.set(advanceMode, forKey: "ig_digest_advance_mode")
        UserDefaults.standard.set(breatherEnabled, forKey: "ig_digest_breather_enabled")
        UserDefaults.standard.set(isGrayscale, forKey: "ig_digest_grayscale_mode")
        UserDefaults.standard.set(hideRanks, forKey: "ig_digest_hide_ranks_mode")
        UserDefaults.standard.set(feedSortOrder, forKey: "ig_digest_sort_order")
        BookmarkController.enforceCachedVideosCap(limit: maxCachedVideos, context: modelContext)
    }
}
