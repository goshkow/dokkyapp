using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text.RegularExpressions;

namespace DokkyLauncher
{
    /// <summary>current.json в корне установки: какая папка версии сейчас активна.</summary>
    [DataContract]
    public sealed class Current
    {
        [DataMember(Name = "v")] public int V { get; set; }
        [DataMember(Name = "version")] public string Version { get; set; }
        [DataMember(Name = "dir")] public string Dir { get; set; }
        [DataMember(Name = "previous")] public string Previous { get; set; }
    }

    /// <summary>
    /// Dokky.exe: читает current.json, запускает app-&lt;версия&gt;\DokkyApp.exe с теми же аргументами
    /// (в том числе koddi://...) и сразу завершается.
    /// </summary>
    public static class Program
    {
        public const int FormatVersion = 1;
        const string AppExe = "DokkyApp.exe";
        const string PortableMarker = "portable.dokky";
        const string DataEnv = "KODDI_DATA_DIR";
        static readonly Regex VersionDir = new Regex(@"^app-\d+\.\d+\.\d+(-beta\.\d+)?$", RegexOptions.CultureInvariant);

        [STAThread]
        static int Main()
        {
            try
            {
                var root = Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory).TrimEnd('\\');
                var appDir = ActiveDir(root, Read(Path.Combine(root, "current.json")));
                var exe = Path.Combine(appDir, AppExe);
                if (!File.Exists(exe)) throw new FileNotFoundException("Нет файла " + Path.Combine(Path.GetFileName(appDir), AppExe) + ".");
                var start = new ProcessStartInfo(exe, Arguments(Environment.CommandLine))
                {
                    UseShellExecute = false,
                    WorkingDirectory = appDir,
                };
                // переносная версия хранит данные рядом с лаунчером, а не в папке версии: та меняется при обновлении
                if (File.Exists(Path.Combine(root, PortableMarker)) && string.IsNullOrEmpty(Environment.GetEnvironmentVariable(DataEnv)))
                    start.EnvironmentVariables[DataEnv] = Path.Combine(root, ".koddi-data");
                Process.Start(start)?.Dispose();
                return 0;
            }
            catch (Exception ex)
            {
                MessageBoxW(IntPtr.Zero, "Не удалось запустить Докки.\n\n" + ex.Message + "\n\nПереустановите программу.", "Докки", 0x10);
                return 1;
            }
        }

        public static Current Read(string path)
        {
            if (!File.Exists(path)) throw new FileNotFoundException("Нет файла current.json рядом с Dokky.exe.");
            if (new FileInfo(path).Length > 64 * 1024) throw new InvalidDataException("Файл current.json повреждён.");
            try
            {
                using (var stream = File.OpenRead(path))
                    return (Current)new DataContractJsonSerializer(typeof(Current)).ReadObject(stream);
            }
            catch (SerializationException)
            {
                throw new InvalidDataException("Файл current.json повреждён.");
            }
        }

        /// <summary>Имени папки из current.json не доверяем: только app-X.Y.Z своей версии и только внутри корня.</summary>
        public static string ActiveDir(string root, Current current)
        {
            if (current == null || current.V != FormatVersion)
                throw new InvalidDataException("Неизвестный формат current.json.");
            if (string.IsNullOrEmpty(current.Dir) || !VersionDir.IsMatch(current.Dir) || current.Dir != "app-" + current.Version)
                throw new InvalidDataException("В current.json неверная папка версии.");
            var dir = Path.GetFullPath(Path.Combine(root, current.Dir));
            if (!string.Equals(Path.GetDirectoryName(dir), root.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("В current.json неверная папка версии.");
            return dir;
        }

        /// <summary>Командная строка без имени лаунчера, как есть: кавычки и koddi:// доходят без пересборки.</summary>
        public static string Arguments(string commandLine)
        {
            var line = commandLine ?? "";
            int i = 0;
            while (i < line.Length && char.IsWhiteSpace(line[i])) i++;
            if (i < line.Length && line[i] == '"')
            {
                // имя программы в кавычках: до следующей кавычки, экранирования в нём нет
                var end = line.IndexOf('"', i + 1);
                i = end < 0 ? line.Length : end + 1;
            }
            else
            {
                while (i < line.Length && !char.IsWhiteSpace(line[i])) i++;
            }
            return line.Substring(i).TrimStart();
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern int MessageBoxW(IntPtr owner, string text, string caption, uint type);
    }
}
