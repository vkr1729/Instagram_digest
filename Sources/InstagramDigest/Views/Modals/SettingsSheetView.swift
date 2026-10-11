import SwiftUI

/// Settings sheet for configuring anti-doomscroll controls and mind guardrails.
public struct SettingsSheetView: View {
    @Environment(\.dismiss) private var dismiss

    @Binding public var timeQuotaMinutes: Int
    @Binding public var enforcementMode: String
    @Binding public var advanceMode: String
    @Binding public var breatherEnabled: Bool
    @Binding public var isGrayscale: Bool
    @Binding public var hideRanks: Bool
    @Binding public var feedSortOrder: String

    public init(
        timeQuotaMinutes: Binding<Int>,
        enforcementMode: Binding<String>,
        advanceMode: Binding<String>,
        breatherEnabled: Binding<Bool>,
        isGrayscale: Binding<Bool>,
        hideRanks: Binding<Bool>,
        feedSortOrder: Binding<String>
    ) {
        self._timeQuotaMinutes = timeQuotaMinutes
        self._enforcementMode = enforcementMode
        self._advanceMode = advanceMode
        self._breatherEnabled = breatherEnabled
        self._isGrayscale = isGrayscale
        self._hideRanks = hideRanks
        self._feedSortOrder = feedSortOrder
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
    }
}
