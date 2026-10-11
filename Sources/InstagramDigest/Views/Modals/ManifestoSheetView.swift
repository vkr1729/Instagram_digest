import SwiftUI

/// Manifesto sheet detailing how Instagram Digest neutralizes the 10 psychological addiction traps.
public struct ManifestoSheetView: View {
    @Environment(\.dismiss) private var dismiss

    public init() {}

    public var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    // Header Card
                    VStack(alignment: .leading, spacing: 8) {
                        Text("📖 The Anti-Doomscroll Briefing")
                            .font(.system(size: 22, weight: .bold))
                            .foregroundColor(.white)

                        Text("Neutralizing the 10 Psychological Traps of Social Media")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Color(red: 1.0, green: 0.8, blue: 0.28)) // Amber

                        Text("Mainstream short-form video algorithms are intentionally engineered as behavioral casinos—optimizing for time-on-screen rather than signal. Inspired by the educational breakdown in \"The Brain Behind Your Phone Addiction. We Caught Him!\" (Satvic Movement), Instagram Digest inverts this model:")
                            .font(.system(size: 13))
                            .foregroundColor(.white.opacity(0.85))
                            .lineSpacing(3)
                    }
                    .padding()
                    .background(Color.white.opacity(0.06))
                    .clipShape(RoundedRectangle(cornerRadius: 16))

                    // The 10 Traps
                    Group {
                        trapRow(number: 1, title: "Infinite Scrolling", status: "Solved", desc: "Replaced with a finite weekly batch and an explicit 'You're All Caught Up! 🎉' milestone screen.")
                        trapRow(number: 2, title: "The Red Colour Trap", status: "Solved", desc: "Zero red badge counts or alarm dots. Replaced with quiet-luxury OLED slate tones and an optional 1-tap Grayscale Detox mode.")
                        trapRow(number: 3, title: "Gamified Friendship", status: "Solved", desc: "Completely severed social graph. Zero streaks, zero interaction scores, zero social obligation loops.")
                        trapRow(number: 4, title: "The Anticipation Trap", status: "Solved", desc: "Deterministic static compilation. Zero slot-machine pull-to-refresh roulette or delayed typing indicators.")
                        trapRow(number: 5, title: "Filtered Reality", status: "Solved", desc: "Whitelist ingestion targeting high-signal creators in science, tech, crafts, and finance, with lifestyle filters.")
                        trapRow(number: 6, title: "Customised Feed", status: "Solved", desc: "Replaces black-box recommendation neural nets with an open, fair-share algorithm capping creators at 4 reels.")
                        trapRow(number: 7, title: "Artificial Urgency", status: "Solved", desc: "Completely ignores 24h disappearing stories and live broadcasts. Asynchronous weekly briefing on your schedule.")
                        trapRow(number: 8, title: "Likes and Followers Count", status: "Solved", desc: "Completely strips like counts, view tallies, follower numbers, and public comment threads from the screen.")
                        trapRow(number: 9, title: "Simplicity (Hypnotic Trance)", status: "Solved", desc: "Introduces active decision friction: manual-advance option, 3-second breathers, and active session time-boxing.")
                        trapRow(number: 10, title: "The Buzz", status: "Solved", desc: "Zero real-time push notifications or vibration pings. 100% quiet, passive, on-demand pull model.")
                    }

                    // Link to video
                    Link(destination: URL(string: "https://youtu.be/FmLJbYBu094")!) {
                        HStack {
                            Image(systemName: "play.circle.fill")
                            Text("Watch \"The Brain Behind Your Phone Addiction\" on YouTube")
                                .fontWeight(.semibold)
                        }
                        .font(.system(size: 13))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Color.white.opacity(0.12))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .padding(.top, 8)
                }
                .padding(16)
            }
            .background(Color.black.ignoresSafeArea())
            .navigationTitle("Philosophy & Manifesto")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                        .foregroundColor(.white)
                        .accessibilityIdentifier("ManifestoDoneButton")
                }
            }
        }
    }

    private func trapRow(number: Int, title: String, status: String, desc: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("\(number). \(title)")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundColor(.white)

                Spacer()

                Text(status)
                    .font(.system(size: 11, weight: .bold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.green.opacity(0.2))
                    .foregroundColor(Color.green)
                    .clipShape(Capsule())
            }

            Text(desc)
                .font(.system(size: 13))
                .foregroundColor(.white.opacity(0.75))
                .lineSpacing(2)
        }
        .padding(12)
        .background(Color.white.opacity(0.04))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}
