// Rasterize one local Quick Look slide with WebKit. No visible window or document scripts.
import AppKit
import WebKit
import CoreGraphics

final class Renderer: NSObject, WKNavigationDelegate {
    var view: WKWebView!
    let source: URL
    let output: URL?
    let width: Double
    let height: Double

    init(source: URL, output: URL?, width: Double, height: Double) {
        self.source = source; self.output = output; self.width = width; self.height = height
        super.init()
    }

    func start() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        let rules = #"[{"trigger":{"url-filter":".*"},"action":{"type":"block"}},{"trigger":{"url-filter":"^file:"},"action":{"type":"ignore-previous-rules"}},{"trigger":{"url-filter":"^data:"},"action":{"type":"ignore-previous-rules"}}]"#
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "local-slide", encodedContentRuleList: rules) { list, error in
            guard let list = list, error == nil else { self.fail("Cannot restrict slide resources") }
            config.userContentController.add(list)
            self.view = WKWebView(frame: CGRect(x: 0, y: 0, width: self.width, height: self.height), configuration: config)
            self.view.navigationDelegate = self
            self.view.loadFileURL(self.source, allowingReadAccessTo: self.source.deletingLastPathComponent())
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let config = WKPDFConfiguration()
        config.rect = CGRect(x: 0, y: 0, width: width, height: height)
        webView.createPDF(configuration: config) { result in
            do {
                let data = try result.get()
                guard let provider = CGDataProvider(data: data as CFData),
                      let pdf = CGPDFDocument(provider), let page = pdf.page(at: 1) else { self.fail("No rendered slide") }
                let targetWidth = 240
                let targetHeight = max(1, Int((Double(targetWidth) * self.height / self.width).rounded()))
                guard let context = CGContext(data: nil, width: targetWidth, height: targetHeight,
                    bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { self.fail("No image context") }
                let rect = CGRect(x: 0, y: 0, width: targetWidth, height: targetHeight)
                context.setFillColor(CGColor(gray: 1, alpha: 1)); context.fill(rect)
                context.concatenate(page.getDrawingTransform(.mediaBox, rect: rect, rotate: 0, preserveAspectRatio: true))
                context.drawPDFPage(page)
                guard let image = context.makeImage(),
                      let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else { self.fail("No image bytes") }
                if let output = self.output { try png.write(to: output, options: .atomic) }
                else { FileHandle.standardOutput.write(png) }
                exit(0)
            } catch { self.fail("Slide rendering failed: \(error)") }
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { fail("Slide load failed") }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { fail("Slide load failed") }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(navigationAction.request.url?.isFileURL == true ? .allow : .cancel)
    }
    func fail(_ message: String) -> Never {
        FileHandle.standardError.write(Data((message + "\n").utf8)); exit(1)
    }
}

let args = CommandLine.arguments
guard args.count == 5, let width = Double(args[2]), let height = Double(args[3]),
      width.isFinite, height.isFinite, width > 0, height > 0, width <= 10000, height <= 10000 else { exit(2) }
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let renderer = Renderer(source: URL(fileURLWithPath: args[1]), output: args[4] == "-" ? nil : URL(fileURLWithPath: args[4]), width: width, height: height)
Timer.scheduledTimer(withTimeInterval: 15, repeats: false) { _ in exit(3) }
renderer.start()
app.run()
