import GapcrossCore
import SwiftUI

/// A marker opens its explanation and evidence. Leads are shown, marked as leads.
struct MarkerDetailView: View {
    let marker: CrossingMarker

    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack(spacing: 12) {
                        MarkerBadge(symbol: marker.symbol)
                        Text(status).font(.headline)
                    }
                    Text(marker.explanation)
                }
                if !marker.evidence.isEmpty {
                    Section(AppText.evidence) {
                        ForEach(Array(marker.evidence.enumerated()), id: \.offset) { _, item in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.claim)
                                HStack {
                                    Text(item.ref).font(.caption.monospaced())
                                    if item.isLead { Text(AppText.lead).font(.caption.bold()) }
                                }
                                .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .navigationTitle(marker.mainStreet)
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var status: String {
        switch marker.symbol {
        case .flagged: AppText.flagged
        case .unknown: AppText.unknown
        case .unflagged: AppText.unflagged
        }
    }
}
