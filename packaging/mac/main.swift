// Veuly's Mac app. Built by packaging/build_mac.py; see AppDelegate for what it does.
import AppKit

signal(SIGPIPE, SIG_IGN)          // a closed pipe or socket is an error to handle, not a reason to end
TestMode.refuseRealLibrary()      // before anything, the log included, touches the data folder
Log.open()

// The interface language chosen in Settings also picks the bundle's (en, es or ca), so what macOS adds to the app
// (the open and save panels, Services, dictation) speaks it too. Empty: the system's own, as usual.
let chosen = DataFolder.chosenLanguage()
if Texts.languages.contains(chosen) {
    var arguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
    arguments["AppleLanguages"] = [chosen]
    UserDefaults.standard.setVolatileDomain(arguments, forName: UserDefaults.argumentDomain)
}

MainActor.assumeIsolated {
    let delegate = AppDelegate()
    NSApplication.shared.delegate = delegate
    withExtendedLifetime(delegate) { NSApplication.shared.run() }
}
