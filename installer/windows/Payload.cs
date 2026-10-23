using System;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace DokkySetup
{
    [DataContract]
    public sealed class Manifest
    {
        [DataMember(Name = "name")] public string Name { get; set; } = "Докки";
        [DataMember(Name = "version")] public string Version { get; set; } = "";
        [DataMember(Name = "exe")] public string Exe { get; set; } = "DokkyApp.exe";
        [DataMember(Name = "size")] public long Size { get; set; }
        [DataMember(Name = "files")] public int Files { get; set; }
    }

    /// <summary>
    /// Архив приложения дописан в конец exe:
    /// [exe установщика][zip][manifest json][int32 длина manifest][int64 длина zip][sha256 zip, 32 байта][«DOKKYSF1»].
    /// Без архива тот же exe работает как программа удаления.
    /// </summary>
    public sealed class Payload
    {
        public const string Magic = "DOKKYSF1";
        const int Tail = 4 + 8 + 32 + 8;

        public string Path { get; private set; }
        public long Offset { get; private set; }
        public long Length { get; private set; }
        public byte[] Sha256 { get; private set; }
        public Manifest Manifest { get; private set; }

        public static Payload Find(string exePath)
        {
            using (var file = File.OpenRead(exePath))
            {
                long end = LogicalEnd(file);
                if (end < Tail) return null;
                var tail = new byte[Tail];
                file.Seek(end - Tail, SeekOrigin.Begin);
                ReadExactly(file, tail);
                if (Encoding.ASCII.GetString(tail, Tail - 8, 8) != Magic) return null;
                int manifestLength = BitConverter.ToInt32(tail, 0);
                long length = BitConverter.ToInt64(tail, 4);
                var hash = new byte[32];
                Array.Copy(tail, 12, hash, 0, 32);
                long manifestStart = end - Tail - manifestLength;
                long offset = manifestStart - length;
                if (manifestLength <= 0 || manifestLength > 64 * 1024 || length <= 0 || offset <= 0)
                    throw new InvalidDataException("Установщик повреждён. Скачайте его заново.");
                var json = new byte[manifestLength];
                file.Seek(manifestStart, SeekOrigin.Begin);
                ReadExactly(file, json);
                Manifest manifest;
                using (var stream = new MemoryStream(json))
                    manifest = (Manifest)new DataContractJsonSerializer(typeof(Manifest)).ReadObject(stream);
                return new Payload { Path = exePath, Offset = offset, Length = length, Sha256 = hash, Manifest = manifest };
            }
        }

        /// <summary>
        /// Конец данных без подписи Authenticode: signtool дописывает её после нашего хвоста
        /// (таблица сертификатов в конце файла, перед ней до 7 нулевых байт выравнивания).
        /// </summary>
        static long LogicalEnd(FileStream file)
        {
            long length = file.Length;
            if (length < 0x40) return length;
            var header = new byte[0x40];
            file.Seek(0, SeekOrigin.Begin);
            ReadExactly(file, header);
            int pe = BitConverter.ToInt32(header, 0x3C);
            if (header[0] != 'M' || header[1] != 'Z' || pe <= 0 || pe > length - 0x100) return length;
            var nt = new byte[0x100];
            file.Seek(pe, SeekOrigin.Begin);
            ReadExactly(file, nt);
            if (BitConverter.ToUInt32(nt, 0) != 0x00004550) return length;
            int optional = 24;
            ushort magic = BitConverter.ToUInt16(nt, optional);
            int security = optional + (magic == 0x20B ? 144 : 128);
            long address = BitConverter.ToUInt32(nt, security);
            long size = BitConverter.ToUInt32(nt, security + 4);
            if (address <= 0 || size <= 0 || address + size != length) return length;
            long end = address;
            var pad = new byte[8];
            int count = (int)Math.Min(7, end);
            file.Seek(end - count, SeekOrigin.Begin);
            ReadExactly(file, pad, count);
            // хвост кончается на «DOKKYSF1», нулей в конце у него нет: нули - выравнивание signtool
            while (count > 0 && pad[count - 1] == 0) { count--; end--; }
            return end;
        }

        /// <summary>Проверка целостности до распаковки: недокачанный файл не должен сломать установленную версию.</summary>
        public void Verify(Action<double> progress, CancellationToken cancel)
        {
            using (var sha = SHA256.Create())
            using (var stream = Open())
            {
                var buffer = new byte[1 << 20];
                long done = 0;
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    cancel.ThrowIfCancellationRequested();
                    sha.TransformBlock(buffer, 0, read, null, 0);
                    done += read;
                    progress(done / (double)Length);
                }
                sha.TransformFinalBlock(buffer, 0, 0);
                if (!Equal(sha.Hash, Sha256))
                    throw new InvalidDataException("Файл установщика повреждён или скачан не полностью. Скачайте его заново.");
            }
        }

        public Stream Open() => new SliceStream(File.OpenRead(Path), Offset, Length);

        static bool Equal(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            int diff = 0;
            for (int i = 0; i < a.Length; i++) diff |= a[i] ^ b[i];
            return diff == 0;
        }

        static void ReadExactly(Stream stream, byte[] buffer) => ReadExactly(stream, buffer, buffer.Length);

        static void ReadExactly(Stream stream, byte[] buffer, int count)
        {
            int done = 0;
            while (done < count)
            {
                int read = stream.Read(buffer, done, count - done);
                if (read <= 0) throw new EndOfStreamException();
                done += read;
            }
        }
    }

    /// <summary>Только чтение части файла: ZipArchive видит архив так, будто он лежит отдельно.</summary>
    sealed class SliceStream : Stream
    {
        readonly Stream inner;
        readonly long start;
        readonly long length;
        long position;

        public SliceStream(Stream inner, long start, long length)
        {
            this.inner = inner;
            this.start = start;
            this.length = length;
            inner.Seek(start, SeekOrigin.Begin);
        }

        public override bool CanRead => true;
        public override bool CanSeek => true;
        public override bool CanWrite => false;
        public override long Length => length;

        public override long Position
        {
            get => position;
            set => Seek(value, SeekOrigin.Begin);
        }

        public override int Read(byte[] buffer, int offset, int count)
        {
            long left = length - position;
            if (left <= 0) return 0;
            if (count > left) count = (int)left;
            int read = inner.Read(buffer, offset, count);
            position += read;
            return read;
        }

        public override long Seek(long offset, SeekOrigin origin)
        {
            long target = origin == SeekOrigin.Begin ? offset : origin == SeekOrigin.Current ? position + offset : length + offset;
            if (target < 0 || target > length) throw new IOException("Выход за пределы архива");
            position = target;
            inner.Seek(start + target, SeekOrigin.Begin);
            return position;
        }

        public override void Flush() { }
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

        protected override void Dispose(bool disposing)
        {
            if (disposing) inner.Dispose();
            base.Dispose(disposing);
        }
    }
}
