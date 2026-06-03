import SwiftUI

struct ImpressionistGradientBackground: View {
    @State private var phase: CGFloat = 0

    var body: some View {
        ZStack {
            // Base visual effect for blur and native HUD feel
            VisualEffectView(material: .hudWindow, blendingMode: .withinWindow)
            
            // Subtle, slow-moving animated gradient inspired by the ImpressionistLLM painting
            // Colors: Deep water green, sky blue, and muted poppy red
            LinearGradient(
                colors: [
                    Color(red: 0.10, green: 0.25, blue: 0.20).opacity(0.35), // Deep lilypad green
                    Color(red: 0.15, green: 0.25, blue: 0.40).opacity(0.35), // Muted sky/water blue
                    Color(red: 0.35, green: 0.10, blue: 0.15).opacity(0.25)  // Subtle deep poppy red
                ],
                startPoint: UnitPoint(x: 0.5 + sin(phase) * 0.5, y: 0.5 + cos(phase) * 0.5),
                endPoint: UnitPoint(x: 0.5 - sin(phase) * 0.5, y: 0.5 - cos(phase) * 0.5)
            )
            .blendMode(.plusLighter) // Ensures it remains subtle and text is highly readable
            .onAppear {
                withAnimation(.linear(duration: 18.0).repeatForever(autoreverses: true)) {
                    phase = .pi * 2
                }
            }
        }
    }
}
