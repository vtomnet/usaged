import SwiftUI
import ServiceManagement

let endpoint = URL(string: "http://100.94.89.105:8787/usage")!  // usaged on mini, tailnet only
let order = ["claude", "codex", "opencode", "google", "kimi", "zai"]
let names = ["claude": "Claude", "codex": "ChatGPT", "google": "Google", "opencode": "OpenCode", "kimi": "Moonshot", "zai": "Z.ai"]
let staleAfter: TimeInterval = 15 * 60

func until(_ d: Date) -> String {
  let s = max(0, Int(d.timeIntervalSinceNow))
  return s >= 86400 ? "\(s / 86400)d \(s % 86400 / 3600)h" : s >= 3600 ? "\(s / 3600)h \(s % 3600 / 60)m" : "\(s / 60)m"
}

struct Window: Decodable { let name: String; let used: Double; let resets_at: Date? }
struct Provider: Decodable { let id: String; let windows: [Window]; let fetched_at: Date?; let error: String? }
struct Usage: Decodable { let providers: [Provider] }

@MainActor @Observable final class Model {
  var usage: Usage?
  var lastError: String?
  var lastLoad: Date?
  var hidden = Set(UserDefaults.standard.stringArray(forKey: "hidden") ?? []) {  // providers not drawn in the menu bar
    didSet { UserDefaults.standard.set(Array(hidden), forKey: "hidden") }
  }
  let logos = Dictionary(uniqueKeysWithValues: order.map { id in
    let img = NSImage(contentsOf: Bundle.main.url(forResource: id, withExtension: "pdf")!)!
    img.isTemplate = true
    return (id, img)
  })
  let decoder: JSONDecoder = { let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d }()

  init() { Task { while true { await load(); try? await Task.sleep(for: .seconds(60)) } } }

  func load() async {
    do {
      let (data, _) = try await URLSession.shared.data(from: endpoint)
      usage = try decoder.decode(Usage.self, from: data)
      lastError = nil
      lastLoad = Date()
    } catch { lastError = error.localizedDescription }
  }

  func provider(_ id: String) -> Provider? { usage?.providers.first { $0.id == id } }

  func dim(_ id: String) -> Bool {
    guard lastError == nil, let p = provider(id), p.error == nil, let t = p.fetched_at else { return true }
    return Date().timeIntervalSince(t) > staleAfter
  }

  // menu bar: [logo][1-3 stacked remaining-bars] per provider, as one template image.
  // read observed state here (not in the lazy draw handler) so SwiftUI tracks it.
  var barImage: NSImage {
    let blocks = order.filter { !hidden.contains($0) }.map { id in (logo: logos[id]!, alpha: dim(id) ? 0.35 : 1.0, left: (provider(id)?.windows ?? []).prefix(3).map { 1 - $0.used }) }
    let logo: CGFloat = 14, barW: CGFloat = 16, barH: CGFloat = 2.5, gap: CGFloat = 1.5, pad: CGFloat = 3, spacing: CGFloat = 8, h: CGFloat = 18
    let block = logo + pad + barW
    let img = NSImage(size: NSSize(width: block * CGFloat(blocks.count) + spacing * CGFloat(blocks.count - 1), height: h), flipped: true) { _ in
      for (i, b) in blocks.enumerated() {
        let x = CGFloat(i) * (block + spacing)
        b.logo.draw(in: NSRect(x: x, y: (h - logo) / 2, width: logo, height: logo), from: .zero, operation: .sourceOver, fraction: b.alpha, respectFlipped: true, hints: nil)
        let total = CGFloat(b.left.count) * barH + CGFloat(max(b.left.count - 1, 0)) * gap
        for (j, left) in b.left.enumerated() {
          let r = NSRect(x: x + logo + pad, y: (h - total) / 2 + CGFloat(j) * (barH + gap), width: barW, height: barH)
          NSColor.black.withAlphaComponent(0.3 * b.alpha).setFill()
          NSBezierPath(roundedRect: r, xRadius: barH / 2, yRadius: barH / 2).fill()
          var f = r; f.size.width = barW * CGFloat(min(max(left, 0), 1))
          NSColor.black.withAlphaComponent(b.alpha).setFill()
          NSBezierPath(roundedRect: f, xRadius: barH / 2, yRadius: barH / 2).fill()
        }
      }
      return true
    }
    img.isTemplate = true
    return img
  }
}

struct Bar: View {
  let fraction: Double
  var body: some View {
    GeometryReader { g in
      ZStack(alignment: .leading) {
        Capsule().fill(.quaternary)
        Capsule().fill(.primary).frame(width: g.size.width * min(max(fraction, 0), 1))
      }
    }
  }
}

struct Panel: View {
  let model: Model
  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      // one grid for all providers so columns line up; header rows span every column
      Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 4) {
        ForEach(order, id: \.self) { id in
          let p = model.provider(id), o = model.dim(id) ? 0.5 : 1
          GridRow {
            HStack(spacing: 6) {
              HStack(spacing: 6) {
                Image(nsImage: model.logos[id]!).renderingMode(.template).resizable().frame(width: 14, height: 14)
                Text(names[id]!).bold()
              }
              .opacity(o)
              Spacer()
              // last shown provider can't be hidden, or the menu bar item would vanish
              Toggle("Show in menu bar", isOn: Binding(
                get: { !model.hidden.contains(id) },
                set: { if $0 { model.hidden.remove(id) } else { model.hidden.insert(id) } }))
                .toggleStyle(.checkbox).labelsHidden()
                .disabled(!model.hidden.contains(id) && model.hidden.count == order.count - 1)
            }
            .gridCellColumns(4)
          }
          .padding(.top, id == order.first ? 0 : 8)
          ForEach(p?.windows ?? [], id: \.name) { w in
            GridRow {
              Text(w.name).foregroundStyle(.secondary).padding(.leading, 20)
              Bar(fraction: 1 - w.used).frame(width: 80, height: 4)
              Text("\(Int(((1 - w.used) * 100).rounded()))%").monospacedDigit().gridColumnAlignment(.trailing)
              Text(w.resets_at.map(until) ?? "").monospacedDigit().foregroundStyle(.secondary).gridColumnAlignment(.trailing)
            }
            .opacity(o)
          }
          if let e = p?.error {
            GridRow { Text(e).font(.caption).foregroundStyle(.red).lineLimit(2).padding(.leading, 20).gridCellColumns(4) }
          }
        }
      }
      Divider()
      HStack {
        Text(model.lastError.map { "Daemon unreachable: \($0)" } ?? model.lastLoad.map { "Updated \($0.formatted(date: .omitted, time: .shortened))" } ?? "Loading…")
          .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        Spacer()
        Button("Refresh") { Task { await model.load() } }.keyboardShortcut("r")
        Button("Quit") { NSApp.terminate(nil) }.keyboardShortcut("q")
      }
    }
    .padding(14)
    .frame(width: 320)
  }
}

@main struct Usagebar: App {
  @State var model = Model()
  init() { try? SMAppService.mainApp.register() }  // launch at login
  var body: some Scene {
    MenuBarExtra { Panel(model: model) } label: { Image(nsImage: model.barImage) }
      .menuBarExtraStyle(.window)
  }
}
