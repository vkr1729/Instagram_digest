import SwiftUI

/// Modal sheet for jumping directly to any curated reel #1 through N.
public struct JumpToReelModalView: View {
    public let totalCount: Int
    public let currentNumber: Int
    public var onJump: (Int) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var inputNumberText: String = ""
    @State private var errorMessage: String? = nil
    @FocusState private var isInputFocused: Bool

    public init(
        totalCount: Int,
        currentNumber: Int = 1,
        onJump: @escaping (Int) -> Void = { _ in }
    ) {
        self.totalCount = totalCount
        self.currentNumber = currentNumber
        self.onJump = onJump
    }

    private var quickJumpNumbers: [Int] {
        guard totalCount > 0 else { return [1] }
        if totalCount <= 6 {
            return Array(1...totalCount)
        }
        var targets = [1]
        let step = max(1, totalCount / 5)
        for i in 1...4 {
            let val = (step * i)
            if val < totalCount && !targets.contains(val) {
                targets.append(val)
            }
        }
        if !targets.contains(totalCount) {
            targets.append(totalCount)
        }
        return targets
    }

    public var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                VStack(spacing: 24) {
                    // Header prompt
                    VStack(spacing: 8) {
                        Image(systemName: "number.circle.fill")
                            .font(.system(size: 48))
                            .foregroundColor(.orange)

                        Text("Jump to Reel")
                            .font(.system(size: 20, weight: .bold))
                            .foregroundColor(.white)

                        Text("Enter a reel number between 1 and \(totalCount)")
                            .font(.system(size: 13))
                            .foregroundColor(.white.opacity(0.6))
                    }
                    .padding(.top, 24)

                    // Input TextField
                    VStack(spacing: 8) {
                        TextField("\(currentNumber)", text: $inputNumberText)
                            .keyboardType(.numberPad)
                            .focused($isInputFocused)
                            .submitLabel(.go)
                            .onSubmit { submitInput() }
                            .font(.system(size: 32, weight: .heavy, design: .monospaced))
                            .multilineTextAlignment(.center)
                            .foregroundColor(.white)
                            .padding(.vertical, 14)
                            .padding(.horizontal, 24)
                            .background(Color.white.opacity(0.1))
                            .clipShape(RoundedRectangle(cornerRadius: 16))
                            .overlay(
                                RoundedRectangle(cornerRadius: 16)
                                    .stroke(Color.orange.opacity(0.5), lineWidth: 1)
                            )
                            .accessibilityIdentifier("JumpToReelTextField")
                            .onChange(of: inputNumberText) { _, _ in
                                // Clear the stale inline error as soon as the user edits.
                                errorMessage = nil
                            }

                        if let err = errorMessage {
                            Text(err)
                                .font(.system(size: 12))
                                .foregroundColor(.red)
                        }
                    }
                    .padding(.horizontal, 48)

                    // Quick Jump Pills
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Quick Jump")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(.white.opacity(0.5))

                        HStack(spacing: 8) {
                            ForEach(quickJumpNumbers, id: \.self) { num in
                                Button("#\(num)") {
                                    performJump(to: num)
                                }
                                .font(.system(size: 12, weight: .bold, design: .monospaced))
                                .foregroundColor(.white)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Color.white.opacity(0.12))
                                .clipShape(Capsule())
                            }
                        }
                    }
                    .padding(.horizontal, 32)

                    Spacer()

                    // Action Button
                    Button {
                        submitInput()
                    } label: {
                        Text("Jump to Reel")
                            .font(.system(size: 16, weight: .bold))
                            .foregroundColor(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(Color.white)
                            .clipShape(Capsule())
                    }
                    .padding(.horizontal, 32)
                    .padding(.bottom, 24)
                    .accessibilityIdentifier("JumpConfirmButton")
                }
            }
            .navigationTitle("Jump")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .foregroundColor(.white.opacity(0.8))
                }
                // numberPad has no Return key: keyboard Done dismisses it.
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") {
                        isInputFocused = false
                    }
                    .foregroundColor(.orange)
                    .accessibilityIdentifier("JumpKeyboardDoneButton")
                }
            }
            .onAppear {
                inputNumberText = "\(currentNumber)"
            }
        }
    }

    /// Validates the text field: empty, non-numeric, zero, negative, and
    /// over-range inputs all surface an inline error instead of jumping.
    private func submitInput() {
        let trimmed = inputNumberText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            errorMessage = "Please enter a reel number"
            return
        }
        guard let parsed = Int(trimmed) else {
            errorMessage = "Please enter a valid number"
            return
        }
        performJump(to: parsed)
    }

    private func performJump(to number: Int) {
        if number >= 1 && number <= totalCount {
            onJump(number - 1) // 0-indexed
            dismiss()
        } else {
            errorMessage = "Number must be between 1 and \(totalCount)"
        }
    }
}
