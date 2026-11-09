// The words of the Mac app's menus and alerts come from web/i18n.js, the file that holds every text of the page in
// English, Spanish and Catalan: one place to translate, and the app speaks the language the page speaks.
import Foundation
import JavaScriptCore

@MainActor
final class Texts {
    nonisolated static let languages = ["en", "es", "ca"]

    private var table: [String: [String: String]] = [:]     // language -> key -> text; "key__work" holds the work wording
    private(set) var lang = "en"
    private(set) var work = false
    var onChange: (() -> Void)?

    /// Runs i18n.js the way the page does, in JavaScriptCore, and keeps every text of the three languages.
    init(i18n: URL) {
        guard let source = try? String(contentsOf: i18n, encoding: .utf8), let js = JSContext() else {
            Log.write("texts: could not read \(i18n.path)")
            return
        }
        // i18n.js is written for a page: give it the three names it touches.
        js.evaluateScript("var window = this; var navigator = { language: 'en' }; var document = { documentElement: {} };")
        js.evaluateScript(source)
        if let error = js.exception { Log.write("texts: \(error)") }
        guard let t = js.objectForKeyedSubscript("t"), let setLang = js.objectForKeyedSubscript("setLang"),
              let setKind = js.objectForKeyedSubscript("setKind") else { return }
        setKind.call(withArguments: ["course"])         // t() of a "__work" key then gives that key's own text
        for lang in Self.languages {
            setLang.call(withArguments: [lang])
            let keys = js.evaluateScript("I18N_KEYS['\(lang)']")?.toArray() as? [String] ?? []
            var texts: [String: String] = [:]
            for key in keys { texts[key] = t.call(withArguments: [key])?.toString() }
            table[lang] = texts
        }
        lang = Self.pick(DataFolder.chosenLanguage())
    }

    /// The language for a setting: en, es or ca, or the system's own language when the setting is empty, the way
    /// the page falls back to the browser's.
    nonisolated static func pick(_ wanted: String) -> String {
        let code = String((wanted.isEmpty ? (Locale.preferredLanguages.first ?? "en") : wanted).prefix(2)).lowercased()
        return languages.contains(code) ? code : "en"
    }

    /// What the page told the app: its language, and whether the work wording ("meeting") is on.
    func set(lang: String, work: Bool) {
        let lang = Self.languages.contains(lang) ? lang : "en"
        guard lang != self.lang || work != self.work else { return }
        self.lang = lang
        self.work = work
        onChange?()
    }

    /// The text for `key` in the current language, `{name}` filled from `vars`. The work twin wins in work mode;
    /// a text missing in a language falls back to English, as in the page.
    func callAsFunction(_ key: String, _ vars: [String: String] = [:]) -> String { self(key, work: work, vars) }

    /// The same, in the wording of one class's own kind (`work`: a meeting of a project), whichever side the page shows.
    func callAsFunction(_ key: String, work: Bool, _ vars: [String: String] = [:]) -> String {
        let here = table[lang] ?? [:], english = table["en"] ?? [:]
        var text = (work ? here[key + "__work"] ?? english[key + "__work"] : nil) ?? here[key] ?? english[key] ?? key
        for (name, value) in vars { text = text.replacingOccurrences(of: "{\(name)}", with: value) }
        return text
    }
}
