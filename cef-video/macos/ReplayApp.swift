import AppKit
import WebKit

@MainActor
final class ReplayApp: NSObject, NSApplicationDelegate, NSWindowDelegate,
    WKNavigationDelegate, WKScriptMessageHandler {
    private let resources = Bundle.main.resourceURL!
    private var window: NSWindow!
    private var webView: WKWebView!
    private var server: Process?
    private var serverOutput: Pipe?
    private var serverURL: URL?
    private var output = Data()
    private var timeout: DispatchWorkItem?
    private var showingAlert = false
    private var quitting = false
    private var loaded = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.preferences.isElementFullscreenEnabled = true
        configuration.userContentController.add(self, name: "replayQuit")
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 750),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false
        )
        window.contentView = webView
        window.delegate = self
        window.title = "Démarrage de CEF Vidéo…"
        window.center()
        window.makeKeyAndOrderFront(nil)
        window.makeFirstResponder(webView)
        NSApp.appearance = NSAppearance(named: .darkAqua)
        NSApp.applicationIconImage = NSImage(contentsOf: resources.appendingPathComponent("assets/cef-favicon.png"))
        installMenu()
        if #available(macOS 14.0, *) {
            NSApp.activate()
        } else {
            NSApp.activate(ignoringOtherApps: true)
        }
        startServer()
    }

    private func installMenu() {
        let menuBar = NSMenu()
        let appItem = NSMenuItem()
        appItem.submenu = NSMenu(title: "CEF Vidéo")
        appItem.submenu?.addItem(withTitle: "Quitter CEF Vidéo", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menuBar.addItem(appItem)

        let editItem = NSMenuItem()
        editItem.submenu = NSMenu(title: "Édition")
        editItem.submenu?.addItem(withTitle: "Couper", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editItem.submenu?.addItem(withTitle: "Copier", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editItem.submenu?.addItem(withTitle: "Coller", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editItem.submenu?.addItem(withTitle: "Tout sélectionner", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        menuBar.addItem(editItem)

        let windowItem = NSMenuItem()
        windowItem.submenu = NSMenu(title: "Fenêtre")
        windowItem.submenu?.addItem(withTitle: "Fermer la fenêtre", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        menuBar.addItem(windowItem)
        NSApp.mainMenu = menuBar
    }

    private func startServer() {
        stopServer()
        serverURL = nil
        loaded = false
        output.removeAll()
        window.title = "Démarrage de CEF Vidéo…"

        let pythonPaths = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
        guard let python = pythonPaths.first(where: FileManager.default.isExecutableFile(atPath:)) else {
            launchError("Python 3 est introuvable. Installe Python 3 avant de redémarrer.")
            return
        }
        let pipe = Pipe()
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.currentDirectoryURL = resources
        process.arguments = ["-B", "-u", resources.appendingPathComponent("serveur_replay.py").path, "--port", "0", "--sans-navigateur"]
        process.standardOutput = pipe
        process.standardError = FileHandle.standardError
        pipe.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
            let data = handle.availableData
            if data.isEmpty { handle.readabilityHandler = nil }
            DispatchQueue.main.async { [weak self, weak process] in
                guard let self, let process, self.server === process else { return }
                self.readServerOutput(data)
            }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async { [weak self] in
                guard let self, self.server === process, !self.quitting else { return }
                self.launchError("Le serveur local s’est arrêté. Redémarre CEF Vidéo.")
            }
        }
        server = process
        serverOutput = pipe
        do {
            try process.run()
        } catch {
            launchError("Impossible de démarrer Python. Vérifie son installation, puis redémarre CEF Vidéo.")
            return
        }

        let deadline = DispatchWorkItem { [weak self, weak process] in
            guard let self, let process, self.server === process, !self.loaded else { return }
            self.launchError("Le lecteur local ne répond pas. Redémarre CEF Vidéo.")
        }
        timeout = deadline
        DispatchQueue.main.asyncAfter(deadline: .now() + 15, execute: deadline)
    }

    private func readServerOutput(_ data: Data) {
        guard serverURL == nil else { return }
        output.append(data)
        let prefix = "CEF_REPLAY_URL="
        while let newline = output.firstIndex(of: 10) {
            let line = String(decoding: output[..<newline], as: UTF8.self).trimmingCharacters(in: .newlines)
            output.removeSubrange(...newline)
            guard line.hasPrefix(prefix),
                let url = URL(string: String(line.dropFirst(prefix.count))),
                url.scheme == "http", url.host == "127.0.0.1", url.port != nil else { continue }
            serverURL = url
            output.removeAll()
            webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
            return
        }
    }

    private func stopServer() {
        timeout?.cancel()
        timeout = nil
        serverOutput?.fileHandleForReading.readabilityHandler = nil
        serverOutput = nil
        let process = server
        server = nil
        if process?.isRunning == true { process?.terminate() }
    }

    private func launchError(_ message: String) {
        guard !showingAlert, !quitting else { return }
        stopServer()
        loaded = false
        showingAlert = true
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Impossible d’ouvrir le lecteur local"
        alert.informativeText = message
        alert.addButton(withTitle: "Redémarrer")
        alert.addButton(withTitle: "Quitter")
        alert.beginSheetModal(for: window) { [weak self] choice in
            guard let self else { return }
            self.showingAlert = false
            if choice == .alertFirstButtonReturn { self.startServer() }
            else { self.quitNow() }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = action.request.url
        let isLocalPage = action.targetFrame?.isMainFrame == true && url?.scheme == "http"
            && url?.host == "127.0.0.1" && url?.port == serverURL?.port
        decisionHandler(isLocalPage ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loaded = true
        timeout?.cancel()
        window.title = "CEF Vidéo"
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        launchError("Impossible de charger le lecteur. Redémarre CEF Vidéo.")
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        launchError("Le lecteur s’est arrêté. Redémarre CEF Vidéo.")
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        requestQuit()
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateNow }
        requestQuit()
        return .terminateCancel
    }

    private func requestQuit() {
        guard !quitting, !showingAlert else { return }
        guard loaded else {
            let alert = NSAlert()
            alert.messageText = "Quitter CEF Vidéo ?"
            alert.addButton(withTitle: "Annuler")
            alert.addButton(withTitle: "Quitter")
            showingAlert = true
            alert.beginSheetModal(for: window) { [weak self] choice in
                self?.showingAlert = false
                if choice == .alertSecondButtonReturn { self?.quitNow() }
            }
            return
        }
        webView.evaluateJavaScript("window.demanderDeQuitter()") { [weak self] _, error in
            if error != nil { self?.launchError("Le lecteur ne répond pas. Redémarre CEF Vidéo.") }
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.name == "replayQuit", message.body as? String == "quit",
            message.webView === webView, message.frameInfo.isMainFrame,
            origin.protocol == "http", origin.host == "127.0.0.1", origin.port == serverURL?.port else { return }
        quitNow()
    }

    private func quitNow() {
        quitting = true
        stopServer()
        NSApp.terminate(nil)
    }
}

@main
struct ReplayMain {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        let delegate = ReplayApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
