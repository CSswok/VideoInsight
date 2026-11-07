import Foundation
import Vision
import AppKit

// 用法: ocrbin <图片目录> <输出json> <fps>
// 依赖 macOS Vision 框架，用于提取视频内嵌硬字幕。
// 编译: swiftc -O ocr.swift -o ocrbin

let args = CommandLine.arguments
guard args.count >= 4 else {
    FileHandle.standardError.write("usage: ocrbin <dir> <out.json> <fps>\n".data(using: .utf8)!)
    exit(2)
}
let dir = args[1]
let outPath = args[2]
let fps = Double(args[3]) ?? 1.0

let fm = FileManager.default
guard let files = try? fm.contentsOfDirectory(atPath: dir) else { exit(1) }
let pngs = files.filter { $0.hasSuffix(".png") }.sorted()

var rows: [[String: Any]] = []

for (i, name) in pngs.enumerated() {
    let path = (dir as NSString).appendingPathComponent(name)
    var text = ""
    if let img = NSImage(contentsOfFile: path),
       let tiff = img.tiffRepresentation,
       let bmp = NSBitmapImageRep(data: tiff),
       let cg = bmp.cgImage {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        try? handler.perform([request])
        if let obs = request.results {
            var parts: [String] = []
            for o in obs {
                if let c = o.topCandidates(1).first { parts.append(c.string) }
            }
            text = parts.joined(separator: " ")
        }
    }
    rows.append(["t": Double(i) / fps, "text": text])
    if i % 200 == 0 {
        FileHandle.standardError.write("[ocr] \(i)/\(pngs.count)\n".data(using: .utf8)!)
    }
}

if let data = try? JSONSerialization.data(withJSONObject: rows, options: [.prettyPrinted, .withoutEscapingSlashes]) {
    try? data.write(to: URL(fileURLWithPath: outPath))
}
