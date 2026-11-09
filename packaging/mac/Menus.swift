// The menu bar of a Mac app. The Edit menu is not decoration: without its items, Cmd+C, Cmd+V and the rest do
// nothing in a web view. Every title comes from web/i18n.js (`mac_menu_*`), so the menus follow the page's language;
// items AppKit adds by itself (Services, dictation, emoji) follow the bundle's language (en, es or ca).
import AppKit

@MainActor
@objc protocol MenuActions {
    func showAbout(_ sender: Any?)
    func showSettings(_ sender: Any?)
    func showHelp(_ sender: Any?)
    func showMainWindow(_ sender: Any?)
    func reloadPage(_ sender: Any?)
    func zoomActual(_ sender: Any?)
    func zoomIn(_ sender: Any?)
    func zoomOut(_ sender: Any?)
}

@MainActor
enum Menus {
    /// Builds the whole bar. `target` handles the app's own actions; the standard ones go to the first responder.
    static func build(_ t: Texts, target: MenuActions) -> NSMenu {
        let bar = NSMenu()
        func item(_ key: String, _ action: Selector?, _ shortcut: String = "", _ modifiers: NSEvent.ModifierFlags = .command,
                  to: AnyObject? = nil, id: String) -> NSMenuItem {
            let i = NSMenuItem(title: t(key), action: action, keyEquivalent: shortcut)
            i.keyEquivalentModifierMask = modifiers
            i.target = to
            i.identifier = NSUserInterfaceItemIdentifier(id)
            return i
        }
        func menu(_ key: String, id: String, _ items: [NSMenuItem]) -> NSMenuItem {
            let top = NSMenuItem(title: t(key), action: nil, keyEquivalent: "")
            top.identifier = NSUserInterfaceItemIdentifier(id)
            let m = NSMenu(title: t(key))
            items.forEach(m.addItem)
            top.submenu = m
            bar.addItem(top)
            return top
        }

        let services = NSMenuItem(title: t("mac_menu_services"), action: nil, keyEquivalent: "")
        services.identifier = NSUserInterfaceItemIdentifier("services")
        services.submenu = NSMenu(title: t("mac_menu_services"))
        NSApp.servicesMenu = services.submenu
        _ = menu("mac_menu_app", id: "app", [
            item("mac_menu_about", #selector(MenuActions.showAbout(_:)), to: target, id: "about"),
            .separator(),
            item("mac_menu_settings", #selector(MenuActions.showSettings(_:)), ",", to: target, id: "settings"),
            .separator(),
            services,
            .separator(),
            item("mac_menu_hide", #selector(NSApplication.hide(_:)), "h", id: "hide"),
            item("mac_menu_hide_others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option], id: "hide-others"),
            item("mac_menu_show_all", #selector(NSApplication.unhideAllApplications(_:)), id: "show-all"),
            .separator(),
            item("mac_menu_quit", #selector(NSApplication.terminate(_:)), "q", id: "quit"),
        ])
        _ = menu("mac_menu_edit", id: "edit", [
            item("mac_menu_undo", Selector(("undo:")), "z", id: "undo"),
            item("mac_menu_redo", Selector(("redo:")), "z", [.command, .shift], id: "redo"),
            .separator(),
            item("mac_menu_cut", #selector(NSText.cut(_:)), "x", id: "cut"),
            item("mac_menu_copy", #selector(NSText.copy(_:)), "c", id: "copy"),
            item("mac_menu_paste", #selector(NSText.paste(_:)), "v", id: "paste"),
            item("mac_menu_paste_plain", #selector(NSTextView.pasteAsPlainText(_:)), "v", [.command, .option, .shift], id: "paste-plain"),
            item("mac_menu_delete", #selector(NSText.delete(_:)), id: "delete"),
            item("mac_menu_select_all", #selector(NSText.selectAll(_:)), "a", id: "select-all"),
        ])
        _ = menu("mac_menu_view", id: "view", [
            item("mac_menu_reload", #selector(MenuActions.reloadPage(_:)), "r", to: target, id: "reload"),
            .separator(),
            item("mac_menu_actual_size", #selector(MenuActions.zoomActual(_:)), "0", to: target, id: "zoom-actual"),
            item("mac_menu_zoom_in", #selector(MenuActions.zoomIn(_:)), "+", to: target, id: "zoom-in"),     // and ⌘= (PageView)
            item("mac_menu_zoom_out", #selector(MenuActions.zoomOut(_:)), "-", to: target, id: "zoom-out"),
            .separator(),
            item("mac_menu_full_screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control], id: "full-screen"),
        ])
        let windowMenu = menu("mac_menu_window", id: "window", [
            item("mac_menu_close", #selector(NSWindow.performClose(_:)), "w", id: "close"),
            item("mac_menu_minimize", #selector(NSWindow.performMiniaturize(_:)), "m", id: "minimize"),
            item("mac_menu_zoom", #selector(NSWindow.performZoom(_:)), id: "zoom"),
            .separator(),
            item("mac_menu_main_window", #selector(MenuActions.showMainWindow(_:)), "1", to: target, id: "main-window"),
            .separator(),
            item("mac_menu_front", #selector(NSApplication.arrangeInFront(_:)), id: "front"),
        ])
        NSApp.windowsMenu = windowMenu.submenu
        let help = menu("mac_menu_help", id: "help", [
            item("mac_menu_help_item", #selector(MenuActions.showHelp(_:)), "?", to: target, id: "help-item"),
        ])
        NSApp.helpMenu = help.submenu
        return bar
    }

    /// The item with this identifier anywhere in the bar (tests press menu items through it).
    static func find(_ id: String, in bar: NSMenu?) -> NSMenuItem? {
        for top in bar?.items ?? [] {
            if top.identifier?.rawValue == id { return top }
            if let found = top.submenu?.items.first(where: { $0.identifier?.rawValue == id }) { return found }
        }
        return nil
    }
}
