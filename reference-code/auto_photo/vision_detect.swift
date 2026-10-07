import Foundation
import Vision
import ImageIO

// No recognized strings leave the process: only geometric candidates are emitted.
do {
    guard CommandLine.arguments.count >= 2 else { throw NSError(domain: "arguments", code: 1) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    let text = VNRecognizeTextRequest()
    text.recognitionLevel = CommandLine.arguments.count > 2 && CommandLine.arguments[2] == "accurate" ? .accurate : .fast
    text.usesLanguageCorrection = false
    let faces = VNDetectFaceRectanglesRequest()
    let handler = VNImageRequestHandler(url: url, orientation: .up, options: [:])
    try handler.perform([text, faces])
    var regions: [[String: Any]] = []
    func append(_ observation: VNDetectedObjectObservation, _ kind: String) {
        let box = observation.boundingBox
        regions.append(["kind": kind, "bbox": [box.minX, box.minY, box.width, box.height], "confidence": observation.confidence])
    }
    for observation in text.results ?? [] { append(observation, "text") }
    for observation in faces.results ?? [] { append(observation, "face") }
    let data = try JSONSerialization.data(withJSONObject: ["regions": regions], options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardOutput.write(Data("{\"error\":\"vision_unavailable\"}".utf8))
    exit(1)
}
