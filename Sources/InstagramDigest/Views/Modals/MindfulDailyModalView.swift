import SwiftUI

/// Mindful completion sheet triggered upon crossing 50 watched reels in a day.
/// Provides a pause trigger and per-day snooze keyed to the local calendar date (YYYY-MM-DD).
public struct MindfulDailyModalView: View {
    public let viewedCount: Int
    public var onTakeABreak: () -> Void
    public var onSnoozeForToday: () -> Void
    public var onDismiss: () -> Void

    public init(
        viewedCount: Int = 50,
        onTakeABreak: @escaping () -> Void,
        onSnoozeForToday: @escaping () -> Void,
        onDismiss: @escaping () -> Void
    ) {
        self.viewedCount = viewedCount
        self.onTakeABreak = onTakeABreak
        self.onSnoozeForToday = onSnoozeForToday
        self.onDismiss = onDismiss
    }

    public var body: some View {
        ZStack {
            Color.black.opacity(0.85).ignoresSafeArea()

            VStack(spacing: 24) {
                // Calming Icon & Progress Ring
                ZStack {
                    Circle()
                        .stroke(Color.white.opacity(0.15), lineWidth: 8)
                        .frame(width: 100, height: 100)

                    Circle()
                        .trim(from: 0.0, to: 1.0)
                        .stroke(
                            AngularGradient(
                                gradient: Gradient(colors: [Color.teal, Color.cyan, Color.blue]),
                                center: .center
                            ),
                            style: StrokeStyle(lineWidth: 8, lineCap: .round)
                        )
                        .frame(width: 100, height: 100)
                        .rotationEffect(.degrees(-90))

                    Image(systemName: "sparkles")
                        .font(.system(size: 36))
                        .foregroundColor(.cyan)
                }
                .padding(.top, 16)

                VStack(spacing: 8) {
                    Text("Daily Milestone Reached")
                        .font(.system(size: 24, weight: .bold))
                        .foregroundColor(.white)

                    Text("You've watched \(viewedCount) reels today. Consider stepping away to recharge.")
                        .font(.system(size: 15))
                        .foregroundColor(.white.opacity(0.8))
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 24)
                }

                VStack(spacing: 12) {
                    // Primary Action: Take a Break
                    Button {
                        onTakeABreak()
                    } label: {
                        HStack {
                            Image(systemName: "pause.circle.fill")
                            Text("Take a Break")
                                .fontWeight(.bold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .background(Color.white)
                        .foregroundColor(.black)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }

                    // Secondary Action: Snooze For Today
                    Button {
                        onSnoozeForToday()
                    } label: {
                        HStack {
                            Image(systemName: "clock.arrow.circlepath")
                            Text("Snooze for Today")
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Color.white.opacity(0.15))
                        .foregroundColor(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }

                    // Tertiary Action: Continue
                    Button {
                        onDismiss()
                    } label: {
                        Text("Continue Watching")
                            .font(.system(size: 14))
                            .foregroundColor(.white.opacity(0.6))
                    }
                    .padding(.top, 4)
                }
                .padding(.horizontal, 24)
            }
            .padding(24)
            .background(.ultraThinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 24))
            .padding(.horizontal, 20)
        }
    }
}
