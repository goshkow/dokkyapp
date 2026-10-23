using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Principal;
using System.Text.RegularExpressions;
using System.Threading;
using Microsoft.Win32;

namespace DokkySetup
{
    public sealed class Options
    {
        public string Dir;
        public bool Desktop = true;
        public bool Launch = true;
        public bool RemoveData;
        /// <summary>для всех пользователей: реестр HKLM и общие ярлыки, нужны права администратора</summary>
        public bool Machine;
        /// <summary>данные того, кто запустил установщик: после запроса прав процесс может идти от другого администратора</summary>
        public string DataDir = Installer.UserDataDir;
        /// <summary>проверка сборки: свои ярлыки и ключ реестра, настоящая установка не трогается</summary>
        public bool Smoke;
    }

    public delegate void Report(double fraction, string status);

    /// <summary>
    /// Раскладка под автообновление:
    /// Dokky.exe (лаунчер), current.json, app-X.Y.Z\ (вся сборка), updates\.
    /// По умолчанию на пользователя без прав администратора; Program Files - для всех, с запросом прав и без автообновления.
    /// </summary>
    public static class Installer
    {
        public const string Publisher = "Goshkow";
        public const string LauncherExe = "Dokky.exe";
        const string CurrentJson = "current.json";
        const string UpdatesDir = "updates";
        const string Marker = ".dokky-install";
        const string UninstallExe = "Uninstall.exe";
        const string UninstallRoot = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\";
        // папки версий и их промежуточные копии (.setup - установщика, .tmp/.old - обновления)
        static readonly Regex VersionDir = new Regex(@"^app-\d+\.\d+\.\d+(-beta\.\d+)?$", RegexOptions.CultureInvariant);
        static readonly Regex VersionDirAny = new Regex(@"^app-\d+\.\d+\.\d+(-beta\.\d+)?(\.(setup|tmp|old))?$", RegexOptions.CultureInvariant);

        public static string DefaultDir =>
            System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Dokky");

        // данные приложения: koddi/config.py, data_dir() (APP_NAME = "Koddi")
        public static string UserDataDir =>
            System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Koddi");

        public static bool IsElevated
        {
            get
            {
                using (var identity = WindowsIdentity.GetCurrent())
                    return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
            }
        }

        public static bool InProgramFiles(string dir)
        {
            var full = System.IO.Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            return new[] { Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86 }
                .Select(Environment.GetFolderPath)
                .Any(root => !string.IsNullOrEmpty(root) && full.StartsWith(root.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase));
        }

        /// <summary>Установка для всех: Program Files или папка, куда без прав администратора не записать.</summary>
        public static bool ForAllUsers(string dir) => InProgramFiles(dir) || (!IsElevated && !Writable(dir));

        static bool Writable(string dir)
        {
            var existing = System.IO.Path.GetFullPath(dir);
            while (!string.IsNullOrEmpty(existing) && !Directory.Exists(existing)) existing = System.IO.Path.GetDirectoryName(existing);
            if (string.IsNullOrEmpty(existing)) return false;
            try
            {
                using (File.Create(System.IO.Path.Combine(existing, ".dokky-" + Guid.NewGuid().ToString("N")), 1, FileOptions.DeleteOnClose)) { }
                return true;
            }
            catch (Exception) { return false; }
        }

        static RegistryKey Root(bool machine) => machine ? Registry.LocalMachine : Registry.CurrentUser;
        static string Key(Options o) => UninstallRoot + (o.Smoke ? "DokkySmoke" : "Dokky");
        static string LinkName(Options o) => o.Smoke ? "Докки (проверка).lnk" : "Докки.lnk";
        static string StartMenuLink(Options o, bool machine) => System.IO.Path.Combine(Environment.GetFolderPath(
            machine ? Environment.SpecialFolder.CommonPrograms : Environment.SpecialFolder.Programs), LinkName(o));
        static string DesktopLink(Options o, bool machine) => System.IO.Path.Combine(Environment.GetFolderPath(
            machine ? Environment.SpecialFolder.CommonDesktopDirectory : Environment.SpecialFolder.DesktopDirectory), LinkName(o));

        public sealed class Existing
        {
            public string Dir;
            public string Version;
            public bool Desktop;
            public bool Machine;
        }

        public static Existing Find(Options o)
        {
            foreach (var machine in new[] { true, false })
            {
                using (var key = Root(machine).OpenSubKey(Key(o)))
                {
                    var dir = key?.GetValue("InstallLocation") as string;
                    if (string.IsNullOrEmpty(dir) || !IsOurs(dir)) continue;
                    return new Existing
                    {
                        Dir = dir,
                        Version = key.GetValue("DisplayVersion") as string ?? "",
                        Desktop = File.Exists(DesktopLink(o, machine)),
                        Machine = machine,
                    };
                }
            }
            return null;
        }

        static bool IsOurs(string dir) =>
            File.Exists(System.IO.Path.Combine(dir, Marker))
            || (File.Exists(System.IO.Path.Combine(dir, LauncherExe)) && File.Exists(System.IO.Path.Combine(dir, CurrentJson)));

        /// <summary>В непустую чужую папку не ставим: иначе замена версии удалила бы файлы пользователя.</summary>
        public static string ResolveTarget(string chosen)
        {
            var dir = System.IO.Path.GetFullPath(chosen.Trim().TrimEnd('\\', '/'));
            if (System.IO.Path.GetPathRoot(dir).TrimEnd('\\') == dir.TrimEnd('\\'))
                return System.IO.Path.Combine(dir, "Dokky");
            if (Directory.Exists(dir) && Directory.EnumerateFileSystemEntries(dir).Any() && !IsOurs(dir))
                return System.IO.Path.Combine(dir, "Dokky");
            return dir;
        }

        public static List<Process> Running(string dir)
        {
            var found = new List<Process>();
            if (string.IsNullOrEmpty(dir)) return found;
            var prefix = System.IO.Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            foreach (var process in Process.GetProcesses())
            {
                try
                {
                    var path = process.MainModule?.FileName;
                    if (path != null && path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                    {
                        found.Add(process);
                        continue;
                    }
                }
                catch (Exception) { }  // чужие и системные процессы закрыты для чтения - это не наши
                process.Dispose();
            }
            return found;
        }

        /// <summary>Сначала просим окно закрыться само, и только затем завершаем: человек сам нажал «Закрыть Докки».</summary>
        public static bool CloseRunning(string dir)
        {
            var running = Running(dir);
            foreach (var process in running.Where(p => p.MainWindowHandle != IntPtr.Zero)) process.CloseMainWindow();
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (DateTime.UtcNow < deadline && running.Any(p => !p.HasExited)) Thread.Sleep(200);
            foreach (var process in running.Where(p => !p.HasExited))
            {
                try { process.Kill(); process.WaitForExit(5000); } catch (Exception) { }
            }
            return Running(dir).Count == 0;
        }

        public static string Install(Payload payload, Options o, Report report, CancellationToken cancel)
        {
            report(0, "Проверяю файлы установщика…");
            payload.Verify(f => report(f * 0.12, "Проверяю файлы установщика…"), cancel);

            var target = ResolveTarget(o.Dir);
            if (o.Machine && !IsElevated) throw new UnauthorizedAccessException("Для установки для всех пользователей нужны права администратора.");
            if (Running(target).Count > 0) throw new IOException("Докки ещё открыт. Закройте его и повторите.");
            var version = payload.Manifest.Version;
            var versionDir = "app-" + version;
            if (!VersionDir.IsMatch(versionDir)) throw new InvalidDataException("Установщик повреждён: неверная версия.");
            Directory.CreateDirectory(target);
            // новая версия раскладывается рядом и встаёт на место только целиком; прежняя работает до переключения current.json
            var staging = System.IO.Path.Combine(target, versionDir + ".setup");
            DeleteDir(staging);
            Directory.CreateDirectory(staging);
            try
            {
                Extract(payload, staging, report, cancel);
                foreach (var name in new[] { LauncherExe, UninstallExe })
                    if (!File.Exists(System.IO.Path.Combine(staging, name))) throw new InvalidDataException("Установщик повреждён: нет " + name + ".");
                if (!Directory.Exists(System.IO.Path.Combine(staging, versionDir))) throw new InvalidDataException("Установщик повреждён: нет " + versionDir + ".");
                cancel.ThrowIfCancellationRequested();
                report(0.93, "Заменяю прежнюю версию…");
                Swap(System.IO.Path.Combine(target, versionDir), System.IO.Path.Combine(staging, versionDir));
                ReplaceFile(System.IO.Path.Combine(staging, LauncherExe), System.IO.Path.Combine(target, LauncherExe));
                ReplaceFile(System.IO.Path.Combine(staging, UninstallExe), System.IO.Path.Combine(target, UninstallExe));
                Directory.CreateDirectory(System.IO.Path.Combine(target, UpdatesDir));
                File.WriteAllText(System.IO.Path.Combine(target, Marker), version);
                WriteCurrent(target, version, versionDir);
            }
            finally
            {
                DeleteDir(staging);
            }
            // полный установщик даёт чистое состояние: остальные версии и брошенные копии обновления больше не нужны
            foreach (var other in Directory.EnumerateDirectories(target))
            {
                var name = System.IO.Path.GetFileName(other);
                if (name != versionDir && VersionDirAny.IsMatch(name)) DeleteDir(other);
            }

            report(0.96, "Создаю ярлыки…");
            var exe = System.IO.Path.Combine(target, LauncherExe);
            Native.CreateShortcut(StartMenuLink(o, o.Machine), exe, target, "Докки");
            if (o.Desktop) Native.CreateShortcut(DesktopLink(o, o.Machine), exe, target, "Докки");
            else TryDelete(DesktopLink(o, o.Machine));
            Register(o, target, payload.Manifest);
            report(1, "Готово");
            return target;
        }

        static void Extract(Payload payload, string staging, Report report, CancellationToken cancel)
        {
            var root = System.IO.Path.GetFullPath(staging).TrimEnd('\\') + "\\";
            using (var zip = new ZipArchive(payload.Open(), ZipArchiveMode.Read))
            {
                long total = Math.Max(1, zip.Entries.Sum(e => e.Length));
                long done = 0;
                var buffer = new byte[1 << 18];
                var lastReport = Environment.TickCount;
                foreach (var entry in zip.Entries)
                {
                    cancel.ThrowIfCancellationRequested();
                    var destination = System.IO.Path.GetFullPath(System.IO.Path.Combine(staging, entry.FullName.Replace('/', '\\')));
                    // архив из установщика, но путь всё равно проверяем: файл не должен лечь за пределы папки
                    if (!destination.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("Установщик повреждён: путь вне папки программы.");
                    if (entry.FullName.EndsWith("/"))
                    {
                        Directory.CreateDirectory(destination);
                        continue;
                    }
                    Directory.CreateDirectory(System.IO.Path.GetDirectoryName(destination));
                    using (var input = entry.Open())
                    using (var output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 16))
                    {
                        int read;
                        while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                        {
                            output.Write(buffer, 0, read);
                            done += read;
                            if (Environment.TickCount - lastReport > 50)
                            {
                                lastReport = Environment.TickCount;
                                report(0.12 + 0.8 * done / total, "Распаковываю Докки…");
                            }
                        }
                    }
                }
            }
        }

        /// <summary>Указатель на активную версию для лаунчера; запись через временный файл, чтобы не оставить половину.</summary>
        static void WriteCurrent(string root, string version, string versionDir)
        {
            var path = System.IO.Path.Combine(root, CurrentJson);
            var temp = path + ".tmp";
            // версия и папка уже проверены шаблоном: экранировать в JSON нечего
            File.WriteAllText(temp, "{\"v\":1,\"version\":\"" + version + "\",\"dir\":\"" + versionDir + "\",\"previous\":null}\n");
            ReplaceFile(temp, path);
        }

        static void ReplaceFile(string from, string to)
        {
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    if (File.Exists(to)) File.Replace(from, to, null, true);
                    else File.Move(from, to);
                    return;
                }
                catch (IOException) when (attempt < 20) { Thread.Sleep(250); }
                catch (UnauthorizedAccessException) when (attempt < 20) { Thread.Sleep(250); }
            }
        }

        /// <summary>Новая папка встаёт на место старой одним переименованием; при сбое прежняя версия возвращается.</summary>
        static void Swap(string target, string staging)
        {
            if (!Directory.Exists(target))
            {
                Directory.Move(staging, target);
                return;
            }
            var previous = target + ".old";
            DeleteDir(previous);
            MoveWithRetry(target, previous);
            try
            {
                MoveWithRetry(staging, target);
            }
            catch
            {
                Directory.Move(previous, target);
                throw;
            }
            DeleteDir(previous);
        }

        static void MoveWithRetry(string from, string to)
        {
            // антивирус и индексатор ненадолго держат только что записанные файлы
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    Directory.Move(from, to);
                    return;
                }
                catch (IOException) when (attempt < 20)
                {
                    Thread.Sleep(250);
                }
                catch (UnauthorizedAccessException) when (attempt < 20)
                {
                    Thread.Sleep(250);
                }
            }
        }

        static void Register(Options o, string dir, Manifest manifest)
        {
            using (var key = Root(o.Machine).CreateSubKey(Key(o)))
            {
                var uninstall = System.IO.Path.Combine(dir, UninstallExe);
                var smoke = o.Smoke ? " --smoke" : "";
                key.SetValue("DisplayName", o.Smoke ? "Докки (проверка)" : "Докки");
                key.SetValue("DisplayVersion", manifest.Version);
                key.SetValue("Publisher", Publisher);
                key.SetValue("DisplayIcon", System.IO.Path.Combine(dir, LauncherExe) + ",0");
                key.SetValue("InstallLocation", dir);
                key.SetValue("UninstallString", "\"" + uninstall + "\" --uninstall" + smoke);
                key.SetValue("QuietUninstallString", "\"" + uninstall + "\" --uninstall --silent" + smoke);
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd", CultureInfo.InvariantCulture));
                key.SetValue("EstimatedSize", (int)Math.Min(int.MaxValue, manifest.Size / 1024), RegistryValueKind.DWord);
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        }

        public static void Uninstall(Options o, Report report)
        {
            var dir = o.Dir;
            if (string.IsNullOrEmpty(dir) || !IsOurs(dir))
                throw new InvalidOperationException("Не нашёл установленный Докки в этой папке.");
            if (NeedsElevation(o, dir)) throw new UnauthorizedAccessException("Для удаления Докки нужны права администратора.");
            if (Running(dir).Count > 0) throw new IOException("Докки ещё открыт. Закройте его и повторите.");
            report(0.2, "Удаляю ярлыки…");
            foreach (var machine in new[] { true, false })
            {
                if (!Registered(o, machine, dir)) continue;
                TryDelete(StartMenuLink(o, machine));
                TryDelete(DesktopLink(o, machine));
            }
            report(0.4, "Удаляю программу…");
            // только своё: лаунчер, указатель, папки версий и обновлений; чужие файлы в корне остаются
            var ours = Directory.EnumerateDirectories(dir).Where(d => VersionDirAny.IsMatch(System.IO.Path.GetFileName(d)))
                .Concat(new[] { System.IO.Path.Combine(dir, UpdatesDir) }).ToList();
            foreach (var sub in ours) DeleteDir(sub);
            var files = new[] { LauncherExe, CurrentJson, CurrentJson + ".tmp", UninstallExe, Marker }.Select(f => System.IO.Path.Combine(dir, f)).ToList();
            foreach (var file in files) TryDelete(file);
            if (ours.Any(Directory.Exists) || files.Any(File.Exists))
                throw new IOException("Не удалось удалить все файлы. Закройте Докки и повторите.");
            if (!Directory.EnumerateFileSystemEntries(dir).Any()) DeleteDir(dir);
            foreach (var machine in new[] { true, false })
                if (Registered(o, machine, dir)) Root(machine).DeleteSubKeyTree(Key(o), false);
            ForgetUserIntegration(dir);
            if (o.RemoveData && !string.IsNullOrEmpty(o.DataDir))
            {
                report(0.8, "Удаляю чаты и настройки…");
                DeleteDir(o.DataDir);
            }
            report(1, "Готово");
        }

        /// <summary>
        /// Автозапуск и ссылки koddi:// приложение записывает само (desktop/autostart.py, desktop/session.py);
        /// убираем их, только если они ведут в удаляемую папку.
        /// </summary>
        static void ForgetUserIntegration(string dir)
        {
            var prefix = System.IO.Path.GetFullPath(dir).TrimEnd('\\') + "\\";
            bool Inside(object value) => value is string text && text.IndexOf(prefix, StringComparison.OrdinalIgnoreCase) >= 0;
            try
            {
                using (var run = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                    if (run != null && Inside(run.GetValue("DokkyApp"))) run.DeleteValue("DokkyApp", false);
                using (var command = Registry.CurrentUser.OpenSubKey(@"Software\Classes\koddi\shell\open\command"))
                    if (command != null && Inside(command.GetValue(""))) Registry.CurrentUser.DeleteSubKeyTree(@"Software\Classes\koddi", false);
            }
            catch (Exception ex) when (ex is UnauthorizedAccessException || ex is System.Security.SecurityException || ex is IOException)
            {
                App.Log(ex.ToString());
            }
        }

        public static bool NeedsElevation(Options o, string dir) =>
            !IsElevated && (Registered(o, true, dir) || InProgramFiles(dir) || !Writable(dir));

        /// <summary>Запись именно этой установки: чужую запись о другой папке не трогаем.</summary>
        static bool Registered(Options o, bool machine, string dir)
        {
            using (var key = Root(machine).OpenSubKey(Key(o)))
            {
                var location = key?.GetValue("InstallLocation") as string;
                return !string.IsNullOrEmpty(location)
                    && string.Equals(System.IO.Path.GetFullPath(location).TrimEnd('\\'), System.IO.Path.GetFullPath(dir).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
            }
        }

        public static void Launch(string dir, string exe)
        {
            var path = System.IO.Path.Combine(dir, exe);
            // из установщика с правами администратора Докки запускается через проводник - с обычными правами пользователя
            var start = IsElevated
                ? new ProcessStartInfo(System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe"), "\"" + path + "\"")
                : new ProcessStartInfo(path) { WorkingDirectory = dir, UseShellExecute = true };
            Process.Start(start)?.Dispose();
        }

        static void TryDelete(string path)
        {
            try { if (File.Exists(path)) File.Delete(path); } catch (Exception) { }
        }

        static void DeleteDir(string dir)
        {
            if (!Directory.Exists(dir)) return;
            for (int attempt = 0; attempt < 10; attempt++)
            {
                try
                {
                    foreach (var file in Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories))
                        File.SetAttributes(file, FileAttributes.Normal);
                    Directory.Delete(dir, true);
                    return;
                }
                catch (DirectoryNotFoundException) { return; }
                catch (IOException) { Thread.Sleep(250); }
                catch (UnauthorizedAccessException) { Thread.Sleep(250); }
            }
        }
    }
}
