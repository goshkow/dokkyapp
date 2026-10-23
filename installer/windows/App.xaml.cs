using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows;

namespace DokkySetup
{
    /// <summary>
    /// Ключи для проверки сборки и тихой установки:
    /// --silent, --dir &lt;папка&gt;, --no-desktop, --uninstall, --remove-data, --smoke, --log &lt;файл&gt;.
    /// Служебные, после запроса прав: --machine, --auto-start, --data-dir &lt;папка&gt;.
    /// Код выхода 0 - успех, 1 - ошибка (подробности в журнале).
    /// </summary>
    public partial class App : Application
    {
        static string logPath = Path.Combine(Path.GetTempPath(), "dokky-setup.log");

        public static void Log(string text)
        {
            try { File.AppendAllText(logPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {text}{Environment.NewLine}"); }
            catch (Exception) { }
        }

        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);
            var args = e.Args;
            bool Has(string name) => args.Any(a => string.Equals(a, name, StringComparison.OrdinalIgnoreCase));
            string Value(string name)
            {
                for (int i = 0; i < args.Length - 1; i++)
                    if (string.Equals(args[i], name, StringComparison.OrdinalIgnoreCase)) return args[i + 1];
                return null;
            }
            logPath = Value("--log") ?? logPath;

            var self = Process.GetCurrentProcess().MainModule.FileName;
            var options = new Options { Smoke = Has("--smoke"), RemoveData = Has("--remove-data"), Desktop = !Has("--no-desktop") };
            Payload payload = null;
            string error = null;
            try { payload = Payload.Find(self); }
            catch (Exception ex) { error = ex.Message; Log(ex.ToString()); }

            var uninstall = Has("--uninstall") || (payload == null && error == null);
            var existing = Installer.Find(options);
            options.Dir = Value("--dir") ?? existing?.Dir ?? (uninstall ? Path.GetDirectoryName(self) : Installer.DefaultDir);
            options.Machine = Has("--machine") || (existing != null && Value("--dir") == null
                ? existing.Machine
                : Installer.ForAllUsers(Installer.ResolveTarget(options.Dir)));
            if (Value("--data-dir") is string data)
            {
                // удаляем только папку данных Докки, а не любую переданную
                if (!Path.IsPathRooted(data) || !string.Equals(Path.GetFileName(data.TrimEnd('\\')), "Koddi", StringComparison.OrdinalIgnoreCase))
                {
                    Log("Пропускаю неожиданную папку данных: " + data);
                    data = null;
                }
                options.DataDir = data;
            }

            if (uninstall && !Has("--relocated") && IsInside(self, options.Dir))
            {
                // программа удаления лежит в удаляемой папке: работаем из копии во временной папке
                Relocate(self, args, options.Dir);
                Shutdown(0);
                return;
            }
            if (Value("--wait") is string pid && int.TryParse(pid, out var parent)) WaitFor(parent);

            if (Has("--silent"))
            {
                Shutdown(RunSilent(uninstall, payload, options, error));
                return;
            }
            var window = new MainWindow(uninstall ? Mode.Uninstall : Mode.Install, payload, options, existing, error, Has("--auto-start"));
            MainWindow = window;
            window.Show();
        }

        static int RunSilent(bool uninstall, Payload payload, Options options, string error)
        {
            try
            {
                if (error != null) throw new InvalidDataException(error);
                Report report = (fraction, status) => { };
                if (uninstall) Installer.Uninstall(options, report);
                else Log("Установлено в " + Installer.Install(payload, options, report, CancellationToken.None));
                return 0;
            }
            catch (Exception ex)
            {
                Log(ex.ToString());
                return 1;
            }
        }

        /// <summary>
        /// Перезапуск себя с правами администратора. Окно до этого шло без прав: так и запуск Докки после установки,
        /// и выбор папки остаются от имени пользователя. false - человек отказался в окне Windows.
        /// </summary>
        public static bool Elevate(IEnumerable<string> args)
        {
            var self = Process.GetCurrentProcess().MainModule.FileName;
            var line = string.Join(" ", args.Select(Quote));
            if (logPath != null) line += " --log " + Quote(logPath);
            try
            {
                Process.Start(new ProcessStartInfo(self, line) { UseShellExecute = true, Verb = "runas", WorkingDirectory = Path.GetTempPath() })?.Dispose();
                return true;
            }
            catch (Win32Exception ex) when (ex.NativeErrorCode == 1223)
            {
                return false;
            }
        }

        static bool IsInside(string file, string dir)
        {
            if (string.IsNullOrEmpty(dir)) return false;
            var prefix = Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            return Path.GetFullPath(file).StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
        }

        static void Relocate(string self, string[] args, string dir)
        {
            var copy = Path.Combine(Path.GetTempPath(), $"DokkyUninstall-{Guid.NewGuid():N}.exe");
            File.Copy(self, copy);
            var rest = args.Where((a, i) => !IsDirArg(args, i)).Select(Quote);
            var line = string.Join(" ", rest.Concat(new[]
            {
                "--uninstall", "--relocated", "--dir", Quote(dir), "--wait", Process.GetCurrentProcess().Id.ToString(),
            }));
            Process.Start(new ProcessStartInfo(copy, line) { UseShellExecute = false, WorkingDirectory = Path.GetTempPath() })?.Dispose();
        }

        static bool IsDirArg(string[] args, int i) =>
            string.Equals(args[i], "--dir", StringComparison.OrdinalIgnoreCase)
            || (i > 0 && string.Equals(args[i - 1], "--dir", StringComparison.OrdinalIgnoreCase));

        static string Quote(string value) =>
            value.Length == 0 || value.Contains(" ") || value.Contains("\"") ? "\"" + value.TrimEnd('\\').Replace("\"", "\\\"") + "\"" : value;

        static void WaitFor(int pid)
        {
            try
            {
                using (var parent = Process.GetProcessById(pid)) parent.WaitForExit(10000);
            }
            catch (ArgumentException) { }  // уже завершился
        }
    }
}
