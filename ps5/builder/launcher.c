/* PS5 Longshot Plus Builder, Windows: PS5LongshotPlusBuilder.exe, the window's start.

   The portable folder's own program: it starts python\pythonw.exe on app\app.py, both beside it, and ends. No console,
   no installer, nothing written outside the folder but the window's saved choices. "--cli ..." runs the build without
   the window (python\python.exe app\pipeline.py ...), waits for it and gives its exit code.
   Built by ps5/builder/package.py with llvm-mingw (-municode -mwindows -static). */
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    (void)inst, (void)prev, (void)show;
    static wchar_t here[32768], cmd[32768 + 600];
    DWORD n = GetModuleFileNameW(NULL, here, 32768);
    if (!n || n >= 32768) return 2;
    wchar_t *slash = wcsrchr(here, L'\\');
    if (!slash) return 2;
    *slash = 0;
    const int cli = !wcsncmp(args, L"--cli", 5) && (args[5] == 0 || args[5] == L' ');
    if (cli) {
        if (!AttachConsole(ATTACH_PARENT_PROCESS)) AllocConsole();
        _snwprintf(cmd, sizeof cmd / sizeof *cmd, L"\"%ls\\python\\python.exe\" -I -u \"%ls\\app\\pipeline.py\"%ls", here, here, args + 5);
    } else {
        _snwprintf(cmd, sizeof cmd / sizeof *cmd, L"\"%ls\\python\\pythonw.exe\" -I \"%ls\\app\\app.py\" %ls", here, here, args);
    }
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, cli, 0, NULL, here, &si, &pi)) {
        MessageBoxW(NULL, L"The builder's own Python did not start. Unpack the whole PS5LongshotPlusBuilder folder and start "
                    L"PS5LongshotPlusBuilder.exe from inside it.", L"PS5 Longshot Plus Builder", MB_ICONERROR);
        return 2;
    }
    DWORD code = 0;
    if (cli) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return (int)code;
}
