// PDF의 쪽마다 그림(PNG) 한 장씩 만드는 도구 (맥에 들어 있는 PDFKit 사용, 따로 설치할 게 없어요)
// 사용: swift tools/pdf_to_png.swift 강의자료.pdf 나올폴더 [가로픽셀=1920]
import Foundation
import PDFKit
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else {
    print("사용법: pdf_to_png.swift 강의자료.pdf 나올폴더 [가로픽셀]")
    exit(2)
}
guard let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    print("PDF를 열 수 없어요: \(args[1])")
    exit(1)
}
let outDir = args[2]
let width: CGFloat = args.count > 3 ? CGFloat(Double(args[3]) ?? 1920) : 1920
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

var made = 0
for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    let scale = width / box.width
    let size = NSSize(width: width, height: (box.height * scale).rounded())
    // 화면 배율과 상관없이 정확한 픽셀 크기로 그리고, 흰 바탕 위에 올려 투명한 부분이 검게 나오지 않게 함
    let pw = Int(size.width), ph = Int(size.height)
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pw, pixelsHigh: ph, bitsPerSample: 8, samplesPerPixel: 4,
                                     hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0),
          let gctx = NSGraphicsContext(bitmapImageRep: rep) else { continue }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = gctx
    let ctx = gctx.cgContext
    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fill(CGRect(x: 0, y: 0, width: CGFloat(pw), height: CGFloat(ph)))
    ctx.saveGState()
    ctx.scaleBy(x: scale, y: scale)
    ctx.translateBy(x: -box.origin.x, y: -box.origin.y)
    page.draw(with: .mediaBox, to: ctx)
    ctx.restoreGState()
    NSGraphicsContext.restoreGraphicsState()
    guard let png = rep.representation(using: .png, properties: [:]) else { continue }
    // 이 쪽에 들어 있는 글자도 함께 저장 (대본을 쪽에 짝지을 때 씀)
    let txt = (page.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    try? txt.write(toFile: String(format: "%@/page_%03d.txt", outDir, i + 1), atomically: true, encoding: .utf8)
    let path = String(format: "%@/page_%03d.png", outDir, i + 1)
    do { try png.write(to: URL(fileURLWithPath: path)); made += 1 } catch { print("저장 실패: \(path)") }
}
print("쪽 수 \(doc.pageCount), 그림 \(made)장")
