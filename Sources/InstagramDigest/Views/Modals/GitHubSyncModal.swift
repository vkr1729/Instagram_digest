import SwiftUI
import SwiftData

public struct GitHubSyncModal: View {
    @Binding public var repo: String
    @Binding public var pat: String
    public let localBookmarks: [BookmarkItem]
    public let context: ModelContext
    public var onSuccess: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var isSyncing: Bool = false
    @State private var errorMessage: String? = nil
    @State private var successMessage: String? = nil

    public init(
        repo: Binding<String>,
        pat: Binding<String>,
        localBookmarks: [BookmarkItem],
        context: ModelContext,
        onSuccess: @escaping (String) -> Void
    ) {
        self._repo = repo
        self._pat = pat
        self.localBookmarks = localBookmarks
        self.context = context
        self.onSuccess = onSuccess
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("GitHub Bookmark Repository").foregroundColor(.white.opacity(0.6))) {
                    TextField("owner/repo (e.g. vkreddy1729-ops/ig-bookmarks-2026)", text: $repo)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }
                .listRowBackground(Color.white.opacity(0.08))

                Section(header: Text("Personal Access Token (PAT)").foregroundColor(.white.opacity(0.6)),
                        footer: Text("Requires repo / contents read & write permissions.").foregroundColor(.white.opacity(0.4))) {
                    SecureField("ghp_... or gho_...", text: $pat)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }
                .listRowBackground(Color.white.opacity(0.08))

                if let err = errorMessage {
                    Section {
                        Text(err)
                            .font(.system(size: 13))
                            .foregroundColor(.red)
                    }
                    .listRowBackground(Color.red.opacity(0.12))
                }

                if let success = successMessage {
                    Section {
                        Text(success)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.green)
                    }
                    .listRowBackground(Color.green.opacity(0.12))
                }

                Section {
                    Button {
                        performSync()
                    } label: {
                        HStack {
                            Spacer()
                            if isSyncing {
                                ProgressView()
                                    .tint(.white)
                                    .padding(.trailing, 8)
                                Text("Syncing...")
                                    .fontWeight(.bold)
                            } else {
                                Image(systemName: "arrow.triangle.2.circlepath")
                                Text("Sync Bookmarks Now")
                                    .fontWeight(.bold)
                            }
                            Spacer()
                        }
                    }
                    .foregroundColor(.white)
                    .disabled(isSyncing || repo.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                .listRowBackground(Color.cyan.opacity(0.8))
            }
            .scrollContentBackground(.hidden)
            .background(Color.black.ignoresSafeArea())
            .navigationTitle("GitHub Bookmark Sync")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") {
                        dismiss()
                    }
                    .foregroundColor(.white.opacity(0.8))
                }
            }
        }
    }

    private func performSync() {
        isSyncing = true
        errorMessage = nil
        successMessage = nil

        Task { @MainActor in
            do {
                let result = try await GitHubBookmarkSyncService.shared.syncWithGitHub(
                    repo: repo,
                    pat: pat,
                    localBookmarks: localBookmarks,
                    context: context
                )
                isSyncing = false
                successMessage = result.message
                onSuccess(result.message)
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) {
                    dismiss()
                }
            } catch {
                isSyncing = false
                errorMessage = error.localizedDescription
            }
        }
    }
}
