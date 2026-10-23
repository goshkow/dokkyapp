$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Drawing
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DokkyNative {
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSE { public int X,Y; public uint Data,Flags,Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Sequential)] public struct KEY { public ushort Code,Scan; public uint Flags,Time; public UIntPtr Extra; }
    [StructLayout(LayoutKind.Explicit)] public struct UNION { [FieldOffset(0)] public MOUSE Mouse; [FieldOffset(0)] public KEY Key; }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint Type; public UNION Value; }
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr h);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint from, uint to, bool attach);
    [DllImport("user32.dll", EntryPoint="GetWindowThreadProcessId")] static extern uint ThreadOf(IntPtr h, IntPtr pid);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token, int kind, out int info, int length, out int returned);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint count, INPUT[] data, int size);
    public static uint Owner(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return p; }
    // 1 - права повышены, 0 - обычный процесс, -2 - доступ закрыт (процесс выше по уровню целостности), -1 - неизвестно
    public static int Elevated(uint pid) {
        IntPtr process = OpenProcess(0x1000, false, pid);
        if (process == IntPtr.Zero) return Marshal.GetLastWin32Error() == 5 ? -2 : -1;
        try {
            IntPtr token;
            if (!OpenProcessToken(process, 8, out token)) return Marshal.GetLastWin32Error() == 5 ? -2 : -1;
            try { int value, size; return GetTokenInformation(token, 20, out value, 4, out size) ? value : -1; } finally { CloseHandle(token); }
        } finally { CloseHandle(process); }
    }
    // фоновому процессу Windows не отдаёт фокус просто так: пробуем по нарастающей, Alt - в последнюю очередь (он активирует меню)
    public static bool Force(IntPtr h) {
        uint target = Owner(h);
        if (IsIconic(h)) ShowWindow(h, 9);
        for (int attempt = 0; attempt < 3; attempt++) {
            if (Owner(GetForegroundWindow()) == target) return true;
            if (attempt == 0) { SetForegroundWindow(h); }
            else if (attempt == 1) {
                uint me = GetCurrentThreadId(), current = ThreadOf(GetForegroundWindow(), IntPtr.Zero);
                bool attached = current != 0 && current != me && AttachThreadInput(me, current, true);
                try { BringWindowToTop(h); SetForegroundWindow(h); } finally { if (attached) AttachThreadInput(me, current, false); }
            } else {
                keybd_event(0x12, 0, 0, UIntPtr.Zero); keybd_event(0x12, 0, 2, UIntPtr.Zero);
                BringWindowToTop(h); SetForegroundWindow(h);
            }
            System.Threading.Thread.Sleep(120);
        }
        return Owner(GetForegroundWindow()) == target;
    }
    static void Send(INPUT value) { if (SendInput(1, new INPUT[]{value}, Marshal.SizeOf(typeof(INPUT))) != 1) throw new Exception("input_blocked"); }
    public static void Key(ushort code, bool down, bool unicode) {
        var i = new INPUT(); i.Type=1; i.Value.Key.Code=unicode?(ushort)0:code; i.Value.Key.Scan=unicode?code:(ushort)0;
        i.Value.Key.Flags=(unicode?4u:0u)|(down?0u:2u); Send(i);
    }
    public static void Mouse(uint flags, int data) { var i=new INPUT(); i.Type=0; i.Value.Mouse.Flags=flags; i.Value.Mouse.Data=unchecked((uint)data); Send(i); }
}
'@
    # вывод только ASCII (\uXXXX): кодировка консоли не портит кириллицу
    function Emit($value, [int]$depth = 4) {
        $json = $value | ConvertTo-Json -Depth $depth -Compress
        [Console]::Out.Write([regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value }))
    }
    function Frame($element) {
        $rect = $element.Current.BoundingRectangle
        # у скрытых элементов прямоугольник бесконечный: в JSON это ломает сверку кадра
        if ($rect.IsEmpty -or [double]::IsInfinity($rect.X) -or [double]::IsInfinity($rect.Width)) { return @(0,0,0,0) }
        return @($rect.X,$rect.Y,$rect.Width,$rect.Height)
    }
    # аргумент командной строки по правилам CommandLineToArgvW: пробелы и кавычки не разваливают список
    function QuoteArgument([string]$value) {
        if ($value.Length -gt 0 -and $value -notmatch '[\s"]') { return $value }
        $escaped = [regex]::Replace($value, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        return '"' + $escaped + '"'
    }
    function TopWindows() {
        $result = [Collections.Generic.List[object]]::new()
        $all = [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children, [Windows.Automation.Condition]::TrueCondition)
        foreach ($w in $all) {
            try {
                $c = $w.Current
                if ($c.NativeWindowHandle -ne 0 -and $c.Name -and -not $c.IsOffscreen) { $result.Add($w) }
            } catch { }
        }
        return ,$result
    }
    function Describe($element) {
        $c = $element.Current
        $protected = $c.IsPassword
        $title = if ($protected) { '[protected]' } else { $c.Name }
        if ($title.Length -gt 160) { $title = $title.Substring(0,160) }
        return @{role=$c.ControlType.ProgrammaticName; title=$title; protected=[bool]$protected; enabled=[bool]$c.IsEnabled;identifier=[string]$c.AutomationId}
    }
    function Identifier($element) { return ($element.GetRuntimeId() -join '.') }
    function Nodes($root) {
        $queue = [Collections.Generic.Queue[object]]::new()
        foreach ($item in $root) { $queue.Enqueue(@{element=$item; depth=0}) }
        $nodes = [Collections.Generic.List[object]]::new()
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $walker = [Windows.Automation.TreeWalker]::ControlViewWalker
        while ($queue.Count -gt 0 -and $nodes.Count -lt 160 -and $clock.Elapsed.TotalSeconds -lt 3) {
            $item = $queue.Dequeue()
            $element = $item.element
            $node = Describe $element
            $node.path = Identifier $element
            $node.frame = Frame $element
            if (-not $node.protected) {
                # текст полей лежит в ValuePattern, а не в Name; пароли не читаются
                try {
                    $pattern = $null
                    if ($element.TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
                        $text = [string]$pattern.Current.Value
                        if ($text) { $node.value = $text.Substring(0, [Math]::Min(160, $text.Length)) }
                    }
                } catch { }
            }
            $nodes.Add(@{element=$element; node=$node})
            if ($item.depth -lt 7 -and -not $node.protected) {
                $child = $walker.GetFirstChild($element)
                $count=0
                while ($null -ne $child -and $count -lt 160) {
                    $queue.Enqueue(@{element=$child;depth=$item.depth+1})
                    $child=$walker.GetNextSibling($child)
                    $count++
                }
            }
        }
        return ,$nodes
    }
    [void][DokkyNative]::SetThreadDpiAwarenessContext([IntPtr]::new(-4))
    $action = $request.action
    if ($action -eq 'status') {
        Emit @{ok=$true; platform='Windows'; accessibility=$true; screen_capture=$true; elevated=([DokkyNative]::Elevated([uint32]$PID) -eq 1); system_boundary='UIPI and interactive desktop'}
        exit
    }
    if ($action -eq 'list_windows') {
        $items = [Collections.Generic.List[object]]::new()
        foreach ($w in (TopWindows)) {
            if ($items.Count -ge 40) { break }
            $owner = [int]$w.Current.ProcessId
            if ($owner -eq $PID) { continue }
            $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
            $title = [string]$w.Current.Name
            $label = $(if ($proc) { $proc.ProcessName } else { '' })
            # фильтр по заголовку или имени процесса: модель получает только нужные окна, а не все открытые
            if ($request.query -and $title.IndexOf([string]$request.query, [StringComparison]::OrdinalIgnoreCase) -lt 0 -and $label.IndexOf([string]$request.query, [StringComparison]::OrdinalIgnoreCase) -lt 0) { continue }
            if ($title.Length -gt 120) { $title = $title.Substring(0,120) }
            $items.Add(@{pid=$owner; process=$(if ($proc) { $proc.ProcessName } else { '' }); title=$title; window_id=[int]$w.Current.NativeWindowHandle; elevated=([DokkyNative]::Elevated([uint32]$owner) -ne 0)})
        }
        Emit @{ok=$true; windows=@($items)} 6
        exit
    }
    if ($action -eq 'launch') {
        if (-not [IO.Path]::IsPathRooted($request.application) -or [IO.Path]::GetExtension($request.application) -ne '.exe') { throw 'invalid_application' }
        $before = @{}
        foreach ($w in (TopWindows)) { $before[[int]$w.Current.NativeWindowHandle] = $true }
        $began = [DateTime]::Now.AddSeconds(-1)
        $start = @{FilePath=$request.application; PassThru=$true}
        if ($request.arguments -and @($request.arguments).Count -gt 0) { $start.ArgumentList = (@($request.arguments) | ForEach-Object { QuoteArgument ([string]$_) }) -join ' ' }
        if ($request.working_directory) { $start.WorkingDirectory = [string]$request.working_directory }
        try { $started = Start-Process @start } catch {
            $inner = $_.Exception.InnerException
            if (($_.Exception.NativeErrorCode -eq 740) -or ($inner -and $inner.NativeErrorCode -eq 740)) { throw 'launch_requires_elevation' }
            throw
        }
        $name = [IO.Path]::GetFileNameWithoutExtension($request.application)
        $found = $null
        $clock = [Diagnostics.Stopwatch]::StartNew()
        # часть программ (Блокнот и Калькулятор в Windows 11, установщики) стартует через заглушку и открывает окно в другом процессе
        while ($null -eq $found -and $clock.Elapsed.TotalSeconds -lt 8) {
            $started.Refresh()
            if (-not $started.HasExited -and $started.MainWindowHandle -ne [IntPtr]::Zero) { $found = $started }
            if ($null -eq $found) { $found = Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.Id -ne $PID -and $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.StartTime -ge $began -and ($_.ProcessName -eq $name -or $started.HasExited)
            } | Select-Object -First 1 }
            if ($null -eq $found -and $started.HasExited) {
                # заглушка завершилась: ищем окно, которого не было до запуска (упакованные программы живут в чужом процессе)
                foreach ($w in (TopWindows)) {
                    if (-not $before.ContainsKey([int]$w.Current.NativeWindowHandle) -and [int]$w.Current.ProcessId -ne $PID) { $found = Get-Process -Id ([int]$w.Current.ProcessId) -ErrorAction SilentlyContinue; break }
                }
            }
            if ($null -ne $found) {
                # окно должно продержаться: у части упакованных программ оно мигает на старте
                Start-Sleep -Milliseconds 400
                $found.Refresh()
                if ($found.HasExited -or $found.MainWindowHandle -eq [IntPtr]::Zero) { $found = $null }
            }
            if ($null -eq $found) { Start-Sleep -Milliseconds 200 }
        }
        if ($null -eq $found) {
            if ($started.HasExited) { throw 'application_exited' }
            $found = $started
        }
        Emit @{ok=$true;pid=$found.Id;identity="$($found.Id):$($found.StartTime.Ticks)";window=($found.MainWindowHandle -ne [IntPtr]::Zero)}
        exit
    }
    # приложение с повышенными правами недоступно обычному помощнику (UIPI): говорим об этом прямо, а не падаем
    $mine = [DokkyNative]::Elevated([uint32]$PID)
    $theirs = [DokkyNative]::Elevated([uint32][int]$request.pid)
    if ($theirs -eq -2 -and $mine -ne 1) { throw 'needs_elevation' }
    if ($theirs -eq -2) { throw 'protected_process' }
    if ($theirs -eq 1 -and $mine -ne 1) { throw 'needs_elevation' }
    $process = Get-Process -Id ([int]$request.pid)
    $identity = "$($process.Id):$($process.StartTime.Ticks)"
    $handle = $process.MainWindowHandle
    if ($handle -eq [IntPtr]::Zero) { throw 'application_unavailable' }
    if ($action -ne 'observe' -and $request.identity -ne $identity) { throw 'stale_application' }
    $root = [Windows.Automation.AutomationElement]::FromHandle($handle)
    $condition=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty,[int]$process.Id)
    $windows=@([Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children,$condition) | Select-Object -First 20)
    if ($action -eq 'observe') {
        if ($null -ne $request.window_id) {
            $windows=@($windows | Where-Object { $_.Current.NativeWindowHandle -eq [int]$request.window_id })
            if ($windows.Count -ne 1) { throw 'stale_window' }
        }
        $nodes=Nodes $windows
        Emit @{ok=$true;pid=$process.Id;identity=$identity;window_id=$handle.ToInt64();windows=@($windows | ForEach-Object { @{window_id=$_.Current.NativeWindowHandle;title=$_.Current.Name} });elements=@($nodes | ForEach-Object {$_.node});truncated=($nodes.Count -ge 160)} 8
        exit
    }
    if ($action -eq 'focus') {
        if (-not [DokkyNative]::Force($handle)) { throw 'focus_failed' }
    } elseif ($action -eq 'screenshot') {
        $handle=[IntPtr]::new([long]$request.window_id)
        if ([DokkyNative]::Owner($handle) -ne $process.Id) { throw 'stale_window' }
        $rect = [DokkyNative+RECT]::new()
        if (-not [DokkyNative]::GetWindowRect($handle,[ref]$rect)) { throw 'window_unavailable' }
        $width=$rect.R-$rect.L; $height=$rect.B-$rect.T
        if ($width -lt 1 -or $height -lt 1 -or $width -gt 8192 -or $height -gt 8192) { throw 'invalid_capture_size' }
        $bitmap=[Drawing.Bitmap]::new($width,$height)
        $graphics=[Drawing.Graphics]::FromImage($bitmap)
        $dc=$graphics.GetHdc()
        try { $captured=[DokkyNative]::PrintWindow($handle,$dc,2) } finally { $graphics.ReleaseHdc($dc);$graphics.Dispose() }
        try {
            if (-not $captured) { throw 'capture_unavailable' }
            $scale=[Math]::Min(1.0,1600.0/[Math]::Max($width,$height))
            $small=[Drawing.Bitmap]::new($bitmap,[int]($width*$scale),[int]($height*$scale))
            $stream=[IO.MemoryStream]::new()
            try {
                $small.Save($stream,[Drawing.Imaging.ImageFormat]::Png)
                if ($stream.Length -gt 1500000) { throw 'capture_too_large' }
                Emit @{ok=$true;png_base64=[Convert]::ToBase64String($stream.ToArray());width=$small.Width;height=$small.Height;coordinate_space='global_physical_pixels';window_frame=@($rect.L,$rect.T,$width,$height)}
            } finally { $stream.Dispose();$small.Dispose() }
        } finally { $bitmap.Dispose() }
        exit
    } else {
        # нажатие и ввод через UI Automation идут в проверенный элемент и не требуют фокуса;
        # синтетический ввод (клавиши, мышь) идёт только в окно, которое реально на переднем плане
        $direct = $action -in @('press','type_element','focus_window')
        if (-not $direct) {
            if ([DokkyNative]::Owner([DokkyNative]::GetForegroundWindow()) -ne $process.Id) { [void][DokkyNative]::Force($handle) }
            if ([DokkyNative]::Owner([DokkyNative]::GetForegroundWindow()) -ne $process.Id) { throw 'target_not_foreground' }
            $focused=[Windows.Automation.AutomationElement]::FocusedElement
            if ($null -ne $focused -and $focused.Current.IsPassword) { throw 'protected_input' }
        }
        if ($direct) {
            $nodes=Nodes $windows
            $match=@($nodes | Where-Object {$_.node.path -eq $request.element_path})
            if ($match.Count -ne 1) { throw 'stale_element' }
            $element=$match[0].element
            $actual=Describe $element
            foreach ($key in @('role','title','protected','enabled','identifier')) { if ($actual[$key] -cne $request.expected.$key) { throw 'stale_element' } }
            if ($actual.protected) { throw 'protected_input' }
            if ($null -ne $request.expected_frame) {
                $frame=Frame $element
                for ($i=0;$i -lt 4;$i++) { if ($frame[$i] -ne $request.expected_frame[$i]) { throw 'stale_element' } }
            }
            if ($action -eq 'focus_window') {
                if ($actual.role -ne 'ControlType.Window') { throw 'not_a_window' }
                $target=[IntPtr]::new([long]$element.Current.NativeWindowHandle)
                if ([DokkyNative]::Owner($target) -ne $process.Id -or -not [DokkyNative]::Force($target)) { throw 'focus_failed' }
                $element.SetFocus()
            } elseif ($action -eq 'press') {
                $pattern=$element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
                $pattern.Invoke()
            } else {
                $pattern=$element.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern)
                $pattern.SetValue($request.text)
            }
        } elseif ($action -eq 'type') {
            foreach ($char in $request.text.ToCharArray()) { [DokkyNative]::Key([uint16]$char,$true,$true);[DokkyNative]::Key([uint16]$char,$false,$true) }
        } elseif ($action -eq 'hotkey') {
            $codes=@{ctrl=17;alt=18;shift=16;cmd=91;enter=13;tab=9;escape=27;backspace=8;space=32;left=37;right=39;up=38;down=40}
            $pressed=[Collections.Generic.List[uint16]]::new()
            try {
                foreach ($key in $request.keys) {
                    $code=$codes[$key]
                    if ($null -eq $code -and $key -cmatch '^[a-z]$') { $code=[int][char]$key.ToUpperInvariant() }
                    if ($null -eq $code) { throw 'invalid_keys' }
                    [DokkyNative]::Key([uint16]$code,$true,$false);$pressed.Add([uint16]$code)
                }
            } finally { for ($i=$pressed.Count-1;$i -ge 0;$i--) { [DokkyNative]::Key($pressed[$i],$false,$false) } }
        } elseif ($action -in @('click','move','scroll')) {
            $point=[DokkyNative+POINT]::new();$point.X=[int]$request.x;$point.Y=[int]$request.y
            if ([DokkyNative]::Owner([DokkyNative]::WindowFromPoint($point)) -ne $process.Id) { throw 'position_outside_target' }
            if (-not [DokkyNative]::SetCursorPos($point.X,$point.Y)) { throw 'position_unavailable' }
            if ($action -eq 'click') {
                $right = $request.button -eq 'right'
                $times = if ($request.clicks -eq 2) { 2 } else { 1 }
                for ($i=0;$i -lt $times;$i++) {
                    if ($right) { [DokkyNative]::Mouse(8,0);[DokkyNative]::Mouse(16,0) } else { [DokkyNative]::Mouse(2,0);[DokkyNative]::Mouse(4,0) }
                }
            }
            if ($action -eq 'scroll') { [DokkyNative]::Mouse(0x800,[int]$request.delta_y) }
        } else { throw 'unsupported_action' }
    }
    Emit @{ok=$true;observe_before_next_action=$true}
} catch {
    # исключения UIA могут содержать текст чужого приложения
    $failure = @{ok=$false;error_code='native_operation_failed'}
    # наружу уходят только собственные короткие коды
    $known = @('needs_elevation','protected_process','application_exited','application_unavailable','stale_application','stale_element','stale_window','target_not_foreground','focus_failed','protected_input','position_outside_target','position_unavailable','invalid_application','invalid_keys','launch_requires_elevation','not_a_window','capture_unavailable','capture_too_large','invalid_capture_size','window_unavailable','input_blocked','unsupported_action')
    $chain = @($_.Exception.Message)
    if ($_.Exception.InnerException) { $chain += $_.Exception.InnerException.Message }
    if ($_.Exception.InnerException -and $_.Exception.InnerException.InnerException) { $chain += $_.Exception.InnerException.InnerException.Message }
    foreach ($text in $chain) {
        if ($text -and ($known -contains $text)) { $failure.error_code = $text; break }
    }
    # текст ошибки только для отладки помощника, в обычной работе его нет
    if ($env:DOKKY_COMPUTER_DEBUG -eq '1') { $failure.detail = [string]$_ }
    Emit $failure
    exit 1
}
