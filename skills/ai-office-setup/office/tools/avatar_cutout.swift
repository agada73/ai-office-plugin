// 아바타 영상에서 사람만 오려 내는 도구 (맥에 들어 있는 Vision 의 사람 분리 기능 사용, 따로 설치할 게 없어요)
// 영상의 프레임마다 "사람이면 흰색, 배경이면 검은색"인 마스크 영상을 만들고, 사람이 차지하는 범위(bbox)를 기록해요.
// 사용: avatar_cutout 아바타.mp4 마스크.mp4 범위.json [처리할구간 "0-12.5,40-52"]
import Foundation
import AVFoundation
import Vision
import CoreImage
import CoreVideo

let args = CommandLine.arguments
guard args.count >= 4 else {
    print("사용법: avatar_cutout 아바타.mp4 마스크.mp4 범위.json [구간 예: 0-12.5,40-52]")
    exit(2)
}
let inURL = URL(fileURLWithPath: args[1])
let outURL = URL(fileURLWithPath: args[2])
let bboxPath = args[3]
var spans: [(Double, Double)] = []
if args.count > 4 {
    for part in args[4].split(separator: ",") {
        let ab = part.split(separator: "-")
        if ab.count == 2, let a = Double(ab[0]), let b = Double(ab[1]) { spans.append((a, b)) }
    }
}

let asset = AVURLAsset(url: inURL)
guard let track = asset.tracks(withMediaType: .video).first else { print("영상 트랙이 없어요"); exit(1) }
let size = track.naturalSize
let width = Int(size.width), height = Int(size.height)

guard let reader = try? AVAssetReader(asset: asset) else { print("영상을 읽을 수 없어요"); exit(1) }
let readerOut = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(readerOut)

try? FileManager.default.removeItem(at: outURL)
guard let writer = try? AVAssetWriter(outputURL: outURL, fileType: .mp4) else { print("마스크 영상을 만들 수 없어요"); exit(1) }
let writerInput = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: width, AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 8_000_000, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel]])
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: writerInput, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA, kCVPixelBufferWidthKey as String: width, kCVPixelBufferHeightKey as String: height])
writer.add(writerInput)
reader.startReading()
writer.startWriting()
writer.startSession(atSourceTime: .zero)

let ciContext = CIContext()
let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = .balanced
request.outputPixelFormat = kCVPixelFormatType_OneComponent8

var minX = 1.0, minY = 1.0, maxX = 0.0, maxY = 0.0
var frames = 0, processed = 0
let start = Date()

func fillBlack(_ buf: CVPixelBuffer) {
    CVPixelBufferLockBaseAddress(buf, [])
    if let base = CVPixelBufferGetBaseAddress(buf) {
        memset(base, 0, CVPixelBufferGetBytesPerRow(buf) * CVPixelBufferGetHeight(buf))
    }
    CVPixelBufferUnlockBaseAddress(buf, [])
}

func scanBox(_ mask: CVPixelBuffer) {
    CVPixelBufferLockBaseAddress(mask, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(mask, .readOnly) }
    guard let base = CVPixelBufferGetBaseAddress(mask) else { return }
    let w = CVPixelBufferGetWidth(mask), h = CVPixelBufferGetHeight(mask), bpr = CVPixelBufferGetBytesPerRow(mask)
    let p = base.assumingMemoryBound(to: UInt8.self)
    for y in 0..<h {
        for x in 0..<w where p[y * bpr + x] > 128 {
            let nx = Double(x) / Double(w), ny = Double(y) / Double(h)
            if nx < minX { minX = nx }; if nx > maxX { maxX = nx }
            if ny < minY { minY = ny }; if ny > maxY { maxY = ny }
        }
    }
}

while let sample = readerOut.copyNextSampleBuffer() {
    autoreleasepool {
        let pts = CMSampleBufferGetPresentationTimeStamp(sample)
        let t = pts.seconds
        guard let pb = CMSampleBufferGetImageBuffer(sample), let pool = adaptor.pixelBufferPool else { return }
        var outBuf: CVPixelBuffer?
        CVPixelBufferPoolCreatePixelBuffer(nil, pool, &outBuf)
        guard let ob = outBuf else { return }
        let inSpan = spans.isEmpty || spans.contains { t >= $0.0 - 0.3 && t <= $0.1 + 0.3 }
        if inSpan {
            let handler = VNImageRequestHandler(cvPixelBuffer: pb, options: [:])
            if (try? handler.perform([request])) != nil, let mask = request.results?.first?.pixelBuffer {
                var ci = CIImage(cvPixelBuffer: mask)
                ci = ci.transformed(by: CGAffineTransform(scaleX: CGFloat(width) / ci.extent.width, y: CGFloat(height) / ci.extent.height))
                ciContext.render(ci, to: ob)
                if processed % 10 == 0 { scanBox(mask) }
                processed += 1
            } else {
                fillBlack(ob)
            }
        } else {
            fillBlack(ob)
        }
        while !writerInput.isReadyForMoreMediaData { usleep(1000) }
        adaptor.append(ob, withPresentationTime: pts)
        frames += 1
    }
}
writerInput.markAsFinished()
let sem = DispatchSemaphore(value: 0)
writer.finishWriting { sem.signal() }
sem.wait()

let box: [String: Any] = ["x": minX, "y": minY, "w": max(0, maxX - minX), "h": max(0, maxY - minY),
                          "frames": frames, "processed": processed, "seconds": Date().timeIntervalSince(start)]
if let data = try? JSONSerialization.data(withJSONObject: box, options: [.prettyPrinted]) {
    try? data.write(to: URL(fileURLWithPath: bboxPath))
}
print("프레임 \(frames)개 중 \(processed)개 처리, \(String(format: "%.1f", Date().timeIntervalSince(start)))초")
