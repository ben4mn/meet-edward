// edward-mediatool — Edward's local media helper (2026-10-11; docs/architecture/media.md).
//
// One small binary over Apple's own frameworks, so Edward can read video,
// animated images and PDFs without ffmpeg or poppler:
//   edward-mediatool probe  <src>
//   edward-mediatool frames <src> <outdir> <count> <maxEdge>
//   edward-mediatool pdf    <src> [maxChars]
//   edward-mediatool selftest <outdir>        (writes a 3-frame GIF and a 2 s movie for tests)
// Prints exactly one JSON object on stdout. Exit 0 = ok, 2 = usage, 3 = failure.
//
// Build: bash backend/scripts/build_mediatool.sh (swiftc -O -swift-version 5
// -parse-as-library -o <bin> edward_mediatool.swift), for the host's architecture
// (arm64 on Apple silicon).

import AVFoundation
import Foundation
import ImageIO
import PDFKit
import UniformTypeIdentifiers

func emit(_ obj: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: obj, options: []) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
}

func fail(_ message: String, code: Int32 = 3) -> Never {
    emit(["ok": false, "error": message])
    exit(code)
}

func writeJPEG(_ image: CGImage, to url: URL, quality: Double = 0.8) -> Bool {
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else {
        return false
    }
    CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
    return CGImageDestinationFinalize(dest)
}

func evenlySpaced(_ n: Int, _ count: Int) -> [Int] {
    if n <= count { return Array(0..<n) }
    if count <= 1 { return [0] }
    return (0..<count).map { Int((Double($0) * Double(n - 1) / Double(count - 1)).rounded()) }
}

/// Frames of a still or animated image (GIF, HEICS, APNG, HEIC, PNG...). Never upscales.
func imageFrames(_ src: URL, outdir: URL, count: Int, maxEdge: Int) -> [String: Any]? {
    guard let isrc = CGImageSourceCreateWithURL(src as CFURL, nil) else { return nil }
    let n = CGImageSourceGetCount(isrc)
    if n < 1 { return nil }
    var width = 0, height = 0
    if let props = CGImageSourceCopyPropertiesAtIndex(isrc, 0, nil) as? [CFString: Any] {
        width = props[kCGImagePropertyPixelWidth] as? Int ?? 0
        height = props[kCGImagePropertyPixelHeight] as? Int ?? 0
    }
    let edge = max(1, min(maxEdge, max(width, height) > 0 ? max(width, height) : maxEdge))
    let opts: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceThumbnailMaxPixelSize: edge,
        kCGImageSourceCreateThumbnailWithTransform: true,
    ]
    var frames: [[String: Any]] = []
    for (i, index) in evenlySpaced(n, count).enumerated() {
        guard let img = CGImageSourceCreateThumbnailAtIndex(isrc, index, opts as CFDictionary) else { continue }
        let url = outdir.appendingPathComponent(String(format: "frame-%02d.jpg", i))
        if writeJPEG(img, to: url) { frames.append(["index": index, "path": url.path]) }
    }
    return ["ok": !frames.isEmpty, "kind": "image", "frame_count": n, "width": width, "height": height,
            "frames": frames]
}

/// Evenly spaced frames of a movie, rotated upright, at most maxEdge on the long side.
func videoFrames(_ src: URL, outdir: URL, count: Int, maxEdge: Int) async -> [String: Any]? {
    let asset = AVURLAsset(url: src)
    guard let duration = try? await asset.load(.duration).seconds, duration.isFinite, duration > 0 else { return nil }
    guard let videoTracks = try? await asset.loadTracks(withMediaType: .video), !videoTracks.isEmpty else { return nil }
    let audioTracks = (try? await asset.loadTracks(withMediaType: .audio)) ?? []
    let gen = AVAssetImageGenerator(asset: asset)
    gen.appliesPreferredTrackTransform = true
    gen.maximumSize = CGSize(width: maxEdge, height: maxEdge)
    let n = max(1, count)
    let tolerance = CMTime(seconds: min(0.5, duration / Double(2 * n)), preferredTimescale: 600)
    gen.requestedTimeToleranceBefore = tolerance
    gen.requestedTimeToleranceAfter = tolerance
    var frames: [[String: Any]] = []
    for i in 0..<n {
        let t = duration * (Double(i) + 0.5) / Double(n)
        do {
            let (img, actual) = try await gen.image(at: CMTime(seconds: t, preferredTimescale: 600))
            let url = outdir.appendingPathComponent(String(format: "frame-%02d.jpg", i))
            if writeJPEG(img, to: url) { frames.append(["t": actual.seconds, "path": url.path]) }
        } catch {
            continue
        }
    }
    return ["ok": !frames.isEmpty, "kind": "video", "duration": duration, "has_audio": !audioTracks.isEmpty,
            "frames": frames]
}

func probe(_ src: URL) async -> [String: Any] {
    let asset = AVURLAsset(url: src)
    if let duration = try? await asset.load(.duration).seconds, duration.isFinite, duration > 0,
       let video = try? await asset.loadTracks(withMediaType: .video), !video.isEmpty {
        let audio = (try? await asset.loadTracks(withMediaType: .audio)) ?? []
        var size = CGSize.zero
        if let natural = try? await video[0].load(.naturalSize), let transform = try? await video[0].load(.preferredTransform) {
            let r = CGRect(origin: .zero, size: natural).applying(transform)
            size = CGSize(width: abs(r.width), height: abs(r.height))
        }
        return ["ok": true, "kind": "video", "duration": duration, "has_audio": !audio.isEmpty,
                "width": Int(size.width), "height": Int(size.height)]
    }
    if let duration = try? await asset.load(.duration).seconds, duration.isFinite, duration > 0,
       let audio = try? await asset.loadTracks(withMediaType: .audio), !audio.isEmpty {
        return ["ok": true, "kind": "audio", "duration": duration, "has_audio": true]
    }
    if let isrc = CGImageSourceCreateWithURL(src as CFURL, nil), CGImageSourceGetCount(isrc) > 0 {
        let props = CGImageSourceCopyPropertiesAtIndex(isrc, 0, nil) as? [CFString: Any] ?? [:]
        return ["ok": true, "kind": "image", "frame_count": CGImageSourceGetCount(isrc),
                "width": props[kCGImagePropertyPixelWidth] as? Int ?? 0,
                "height": props[kCGImagePropertyPixelHeight] as? Int ?? 0]
    }
    if let doc = PDFDocument(url: src) {
        return ["ok": true, "kind": "pdf", "pages": doc.pageCount]
    }
    return ["ok": false, "error": "not a movie, image or PDF this Mac can read"]
}

func pdfText(_ src: URL, maxChars: Int) -> [String: Any] {
    guard let doc = PDFDocument(url: src) else { return ["ok": false, "error": "not a readable PDF"] }
    var text = ""
    for i in 0..<doc.pageCount {
        if text.count >= maxChars { break }
        if let page = doc.page(at: i), let s = page.string { text += s + "\n" }
    }
    return ["ok": true, "kind": "pdf", "pages": doc.pageCount, "text": String(text.prefix(maxChars))]
}

/// A 2-second, 64x48 H.264 movie of three solid colours, for tests (no personal media needed).
func writeTestMovie(_ url: URL) async -> Bool {
    try? FileManager.default.removeItem(at: url)
    guard let writer = try? AVAssetWriter(outputURL: url, fileType: .mov) else { return false }
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
        AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: 64, AVVideoHeightKey: 48,
    ])
    input.expectsMediaDataInRealTime = false
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
        kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
        kCVPixelBufferWidthKey as String: 64, kCVPixelBufferHeightKey as String: 48,
    ])
    guard writer.canAdd(input) else { return false }
    writer.add(input)
    guard writer.startWriting() else { return false }
    writer.startSession(atSourceTime: .zero)
    for frame in 0..<20 {
        while !input.isReadyForMoreMediaData { try? await Task.sleep(nanoseconds: 2_000_000) }
        var buffer: CVPixelBuffer?
        guard let pool = adaptor.pixelBufferPool,
              CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer) == kCVReturnSuccess, let pb = buffer else { return false }
        CVPixelBufferLockBaseAddress(pb, [])
        let colour: UInt8 = UInt8((frame * 12) % 255)
        memset(CVPixelBufferGetBaseAddress(pb), Int32(colour), CVPixelBufferGetDataSize(pb))
        CVPixelBufferUnlockBaseAddress(pb, [])
        adaptor.append(pb, withPresentationTime: CMTime(value: CMTimeValue(frame), timescale: 10))
    }
    input.markAsFinished()
    await writer.finishWriting()
    return writer.status == .completed
}

func selftest(_ outdir: URL) async -> [String: Any] {
    let movie = outdir.appendingPathComponent("selftest.mov")
    let movieOK = await writeTestMovie(movie)
    let url = outdir.appendingPathComponent("selftest.gif")
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.gif.identifier as CFString, 3, nil),
          let space = CGColorSpace(name: CGColorSpace.sRGB) else { return ["ok": false, "error": "no GIF writer"] }
    for rgb in [(255, 0, 0), (0, 255, 0), (0, 0, 255)] {
        guard let ctx = CGContext(data: nil, width: 64, height: 48, bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { continue }
        ctx.setFillColor(red: CGFloat(rgb.0) / 255, green: CGFloat(rgb.1) / 255, blue: CGFloat(rgb.2) / 255, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: 64, height: 48))
        if let img = ctx.makeImage() {
            CGImageDestinationAddImage(dest, img, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: 0.1]] as CFDictionary)
        }
    }
    let gifOK = CGImageDestinationFinalize(dest)
    return ["ok": gifOK && movieOK, "gif": url.path, "movie": movie.path, "gif_ok": gifOK, "movie_ok": movieOK]
}

@main
struct EdwardMediaTool {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 3 else { fail("usage: edward-mediatool probe|frames|pdf|selftest <src> ...", code: 2) }
        let command = args[1]
        let src = URL(fileURLWithPath: args[2])
        switch command {
        case "probe":
            emit(await probe(src))
        case "frames":
            guard args.count >= 6, let count = Int(args[4]), let maxEdge = Int(args[5]) else {
                fail("usage: edward-mediatool frames <src> <outdir> <count> <maxEdge>", code: 2)
            }
            let outdir = URL(fileURLWithPath: args[3], isDirectory: true)
            try? FileManager.default.createDirectory(at: outdir, withIntermediateDirectories: true)
            if let video = await videoFrames(src, outdir: outdir, count: count, maxEdge: maxEdge) {
                emit(video)
            } else if let image = imageFrames(src, outdir: outdir, count: count, maxEdge: maxEdge) {
                emit(image)
            } else {
                fail("no frames: not a movie or image this Mac can read")
            }
        case "pdf":
            let maxChars = args.count >= 4 ? Int(args[3]) ?? 20000 : 20000
            emit(pdfText(src, maxChars: maxChars))
        case "selftest":
            try? FileManager.default.createDirectory(at: src, withIntermediateDirectories: true)
            emit(await selftest(src))
        default:
            fail("unknown command \(command)", code: 2)
        }
    }
}
