import AVFAudio
import Flutter
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    GeneratedPluginRegistrant.register(with: self)
    let audioSession = AVAudioSession.sharedInstance()
    try? audioSession.setCategory(
      .playback,
      mode: .spokenAudio,
      options: [.allowAirPlay, .allowBluetoothA2DP]
    )
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
}
