import argparse
import json
import multiprocessing
import os
import platform as platform_info
import sys
from collections.abc import Callable

import flet as ft
from dotenv import load_dotenv
from flet.version import flet_version
from screeninfo import get_monitors

from app.app_manager import App, execute_dir
from app.auth.auth_manager import AuthManager
from app.core.runtime.backend_services import BackendServices
from app.core.runtime.bundled_env import patch_macos_flet_launcher, setup_bundled_flet_view
from app.core.runtime.paths import config_dir, prepend_user_bin_dirs, resource_dir
from app.lifecycle.app_close_handler import handle_app_close
from app.lifecycle.tray_manager import TrayManager
from app.ui.components.common.save_progress_overlay import SaveProgressOverlay
from app.ui.layout.responsive_layout import setup_responsive_layout
from app.ui.views.login_view import LoginPage
from app.utils.logger import logger, startup_logger

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6006
MACOS_WINDOW_SCALE = 0.56
WINDOWS_WINDOW_SCALE = 0.68
MIN_WIDTH = 950
MIN_HEIGHT = 620
ASSETS_DIR = "assets"


def get_desktop_window_scale(page: ft.Page) -> float:
    is_macos = page.platform is not None and page.platform.value == "macos"
    return MACOS_WINDOW_SCALE if is_macos else WINDOWS_WINDOW_SCALE


async def setup_desktop_window(page: ft.Page, app: App) -> None:
    if page.web:
        return

    page.window.icon = os.path.join(resource_dir, ASSETS_DIR, "icon.ico")
    page.window.skip_task_bar = False
    page.window.always_on_top = False
    page.focused = True

    try:
        window_scale = get_desktop_window_scale(page)
        page.window.min_width = MIN_WIDTH
        page.window.min_height = max(MIN_HEIGHT, MIN_WIDTH * window_scale)

        if app.settings.user_config.get("remember_window_size"):
            window_width = app.settings.user_config.get("window_width")
            window_height = app.settings.user_config.get("window_height")
            if window_width and window_height:
                page.window.width = int(window_width)
                page.window.height = int(window_height)
            else:
                screen = get_monitors()[0]
                page.window.width = int(screen.width * window_scale)
                page.window.height = int(screen.height * window_scale)
        else:
            screen = get_monitors()[0]
            page.window.width = int(screen.width * window_scale)
            page.window.height = int(screen.height * window_scale)

        page.update()
        await page.window.center()
        await page.window.to_front()
        page.window.visible = True
        page.update()
    except IndexError:
        logger.warning("No monitors detected, using default window size.")


def get_route_handler() -> dict[str, str]:
    return {
        "/": "home",
        "/home": "home",
        "/recordings": "recordings",
        "/settings": "settings",
        "/storage": "storage",
        "/about": "about",
    }


def handle_route_change(page: ft.Page, app: App) -> Callable:
    route_map = get_route_handler()

    def route_change(e: ft.RouteChangeEvent) -> None:
        tr = ft.TemplateRoute(e.route)
        page_name = route_map.get(tr.route)
        if page_name:
            page.run_task(app.switch_page, page_name)

    return route_change


def handle_window_event(page: ft.Page, app: App, save_progress_overlay: "SaveProgressOverlay") -> Callable:
    async def on_window_event(e) -> None:
        if e.type == ft.WindowEventType.CLOSE:
            if app.settings.user_config.get("remember_window_size"):
                app.settings.user_config["window_width"] = page.window.width
                app.settings.user_config["window_height"] = page.window.height
                await app.config_manager.save_user_config(app.settings.user_config)
            await handle_app_close(page, app, save_progress_overlay)

    return on_window_event


def handle_disconnect(page: ft.Page, app: App) -> Callable:
    """Handle disconnection for web mode."""

    async def disconnect(_: ft.ControlEvent) -> None:
        page.pubsub.unsubscribe_all()
        app.settings.user_config["last_route"] = page.route
        await app.config_manager.save_user_config(app.settings.user_config)
        logger.info(f"Saved last route: {page.route}")

        if app.services is not None:
            app.services.unregister_ui_bridge(app)

    return disconnect


def handle_page_resize(page: ft.Page, app: App) -> Callable:
    """handle page resize"""

    def on_resize(_: ft.ControlEvent) -> None:
        setup_responsive_layout(page, app)
        page.update()

    return on_resize


@startup_logger.catch(reraise=True)
async def main(page: ft.Page) -> None:
    startup_logger.info("Page session entered: web={}", page.web)
    page.title = "StreamCap"

    _services = BackendServices.get()
    startup_logger.info("Creating page application")
    app = App(page, services=_services)
    startup_logger.info("Page application created")
    page.data = app
    app.is_web_mode = page.web
    app.is_mobile = False
    await setup_desktop_window(page, app)
    startup_logger.info("Desktop window setup completed")

    if not page.web:
        try:
            app.tray_manager = TrayManager(app)
            logger.info("Tray manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize tray manager: {e}")

    theme_mode = app.settings.user_config.get("theme_mode", "light")
    if theme_mode == "dark":
        page.theme_mode = ft.ThemeMode.DARK
    else:
        page.theme_mode = ft.ThemeMode.LIGHT

    save_progress_overlay = SaveProgressOverlay(app)
    page.overlay.append(save_progress_overlay.overlay)

    async def load_app():
        if page.web:
            setup_responsive_layout(page, app)
            page.on_resize = handle_page_resize(page, app)
            page.on_disconnect = handle_disconnect(page, app)

        page.add(app.complete_page)

        page.on_route_change = handle_route_change(page, app)
        page.window.prevent_close = True
        page.window.on_event = handle_window_event(page, app, save_progress_overlay)
        if page.web:
            rm = _services.recording_manager
            if rm is not None and not rm.is_periodic_task_running():
                logger.info("Starting periodic tasks for the first time in web mode (via session)")
                page.run_task(app.start_periodic_tasks)
            else:
                logger.info("Periodic tasks already running (BackendServices), skipping")
        else:
            logger.info("Starting periodic tasks in desktop mode")
            page.run_task(app.start_periodic_tasks)

            if page.platform and page.platform.value == "windows":
                if app.tray_manager is not None:
                    try:
                        app.tray_manager.start(page)
                    except Exception as err:
                        logger.error(f"Failed to start tray manager: {err}")

        page.update()

        if page.route in ["/", ""]:
            last_route = app.settings.user_config.get("last_route", "/home")
            logger.info(f"Restored last route: {last_route}")
            await page.push_route(last_route)

        if page.web:
            page.run_task(app.switch_page, page.route[1:])

    if page.web:
        auth_manager = AuthManager(app)
        app.auth_manager = auth_manager
        await auth_manager.initialize()

        login_required = app.settings.get_config_value("login_required", False)

        if login_required:
            session_token = await page.shared_preferences.get("session_token")
            if not session_token or not auth_manager.validate_session(session_token):

                async def on_login_success(token):
                    _session_info = auth_manager.active_sessions.get(token, {})
                    app.current_username = _session_info.get("username")

                    page.controls.clear()
                    await load_app()

                page.controls.clear()

                login_page = LoginPage(page, auth_manager, on_login_success)
                page.add(login_page.get_view())
                return
            else:
                session_info = auth_manager.active_sessions.get(session_token, {})
                app.current_username = session_info.get("username")
        else:
            app.current_username = "admin"

    await load_app()
    startup_logger.info("Page main handler completed")


@startup_logger.catch(reraise=True)
def run() -> None:
    with open(config_dir / "version.json", encoding="utf-8") as file:
        app_version = json.load(file)["version_updates"][0]["version"]
    build_info_path = os.path.join(resource_dir, "build_info.json")
    build_id = "source"
    if os.path.isfile(build_info_path):
        with open(build_info_path, encoding="utf-8") as file:
            build_id = json.load(file)["build_id"]
    startup_logger.info(
        "Startup: version={}, build={}, executable={}, frozen={}, pid={}, cwd={}",
        app_version,
        build_id,
        sys.executable,
        getattr(sys, "frozen", False),
        os.getpid(),
        os.getcwd(),
    )
    startup_logger.info(
        "Runtime: system={}, architecture={}, Python={}, Flet={}",
        platform_info.platform(),
        platform_info.machine(),
        platform_info.python_version(),
        flet_version,
    )
    load_dotenv()
    platform = os.getenv("PLATFORM")
    default_host = os.getenv("HOST", DEFAULT_HOST)
    default_port = int(os.getenv("PORT", DEFAULT_PORT))
    assets_dir = os.path.join(resource_dir, ASSETS_DIR)
    prepend_user_bin_dirs()

    parser = argparse.ArgumentParser(description="Run the Flet app with optional web mode.")
    parser.add_argument("--web", action="store_true", help="Run the app in web mode")
    parser.add_argument("--host", type=str, default=default_host, help=f"Host address (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port number (default: {default_port})")
    args = parser.parse_args()

    startup_logger.info("Backend initialization starting: data_dir={}, config_dir={}", execute_dir, config_dir)
    services = BackendServices.bootstrap(execute_dir)
    startup_logger.info("Backend initialization completed")

    is_web = args.web or platform == "web"
    if is_web:
        services.start_background_loop()
        logger.debug("Running in web mode on http://" + args.host + ":" + str(args.port))
        ft.run(
            main=main,
            view=ft.AppView.WEB_BROWSER,
            host=args.host,
            port=args.port,
            assets_dir=assets_dir,
            web_renderer=ft.WebRenderer.CANVAS_KIT,
            no_cdn=True,
        )
    else:
        startup_logger.info("Preparing desktop client")
        setup_bundled_flet_view()
        patch_macos_flet_launcher()
        startup_logger.info("Starting Flet desktop server and client")
        ft.run(main=main, view=ft.AppView.FLET_APP_HIDDEN, assets_dir=assets_dir)
    startup_logger.info("Flet runner exited")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    run()
