using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media.Animation;

namespace DokkySetup
{
    public enum Mode { Install, Uninstall }

    public partial class MainWindow : Window
    {
        readonly Mode mode;
        readonly Payload payload;
        readonly Options options;
        readonly Installer.Existing existing;
        CancellationTokenSource cancel;
        bool busy;
        bool cancelling;
        string installedDir;
        Action retry;
        static readonly IEasingFunction Ease = new CubicEase { EasingMode = EasingMode.EaseOut };

        public MainWindow(Mode mode, Payload payload, Options options, Installer.Existing existing, string error = null, bool autoStart = false)
        {
            InitializeComponent();
            this.mode = mode;
            this.payload = payload;
            this.options = options;
            this.existing = existing;
            SourceInitialized += (s, e) => Native.StyleWindow(new WindowInteropHelper(this).Handle);
            Loaded += (s, e) =>
            {
                if (error != null) ShowError(error, null);
                // копия с правами администратора сразу продолжает то, что человек выбрал в окне без прав
                else if (autoStart && mode == Mode.Uninstall) { RemoveDataBox.IsChecked = options.RemoveData; OnUninstall(this, null); }
                else if (autoStart) { DesktopBox.IsChecked = options.Desktop; OnInstall(this, null); }
                else if (mode == Mode.Uninstall) ShowUninstall();
                else ShowWelcome();
            };
        }

        // ---------- экраны ----------
        void ShowWelcome()
        {
            var version = payload.Manifest.Version;
            CaptionText.Text = "Установка";
            if (existing != null)
            {
                var same = existing.Version == version;
                SetHero(Face.Mark, "Докки", same ? $"Переустановка версии {version}" : $"Обновление {existing.Version} → {version}");
                InstallButton.Content = same ? "Переустановить" : "Обновить";
                DesktopBox.IsChecked = existing.Desktop;
            }
            else
            {
                SetHero(Face.Mark, "Докки", "Установка на этот компьютер");
            }
            UpdateFolder();
            Switch(WelcomeBody);
        }

        void ShowProgress(string title)
        {
            SetHero(Face.Mark, title, "Это займёт около минуты");
            StatusText.Text = "";
            PercentText.Text = "";
            Fill.Width = 0;
            CancelButton.Visibility = mode == Mode.Install ? Visibility.Visible : Visibility.Collapsed;
            CancelButton.IsEnabled = true;
            Switch(ProgressBody);
            Breathe(true);
        }

        void ShowDone()
        {
            Breathe(false);
            var where = options.Desktop ? "в меню «Пуск» и на рабочем столе" : "в меню «Пуск»";
            SetHero(Face.Done, "Готово", $"Докки установлен. Ярлык есть {where}.");
            Switch(DoneBody);
        }

        void ShowRunning(Action next)
        {
            Breathe(false);
            retry = next;
            SetHero(Face.Mark, "Докки сейчас открыт", "Закройте его, чтобы продолжить. Задачи, которые сейчас выполняются, прервутся.");
            Switch(RunningBody);
        }

        void ShowError(string message, Action again)
        {
            Breathe(false);
            retry = again;
            CaptionText.Text = mode == Mode.Install ? "Установка" : "Удаление";
            SetHero(Face.Error, "Не получилось", message);
            RetryButton.Visibility = again != null ? Visibility.Visible : Visibility.Collapsed;
            Switch(ErrorBody);
        }

        void ShowUninstall()
        {
            CaptionText.Text = "Удаление";
            var version = existing?.Version;
            SetHero(Face.Mark, "Удалить Докки?", string.IsNullOrEmpty(version)
                ? "Программа и ярлыки будут удалены с компьютера."
                : $"Версия {version} и ярлыки будут удалены с компьютера.");
            Switch(UninstallBody);
        }

        void ShowRemoved()
        {
            Breathe(false);
            SetHero(Face.Done, "Докки удалён", options.RemoveData
                ? "Программа, чаты и настройки удалены."
                : "Чаты и настройки сохранены: они вернутся, если установить Докки снова.");
            Switch(CloseBody);
        }

        enum Face { Mark, Done, Error }

        void SetHero(Face kind, string title, string subtitle)
        {
            Mark.Visibility = kind == Face.Error ? Visibility.Collapsed : Visibility.Visible;
            ErrorMark.Visibility = kind == Face.Error ? Visibility.Visible : Visibility.Collapsed;
            DoneMark.Visibility = kind == Face.Done ? Visibility.Visible : Visibility.Collapsed;
            TitleText.Text = title;
            TitleText.FontSize = title.Length > 14 ? 22 : 30;
            SubtitleText.Text = subtitle;
            Fade(Hero, 0);
        }

        void Switch(FrameworkElement body)
        {
            foreach (UIElement child in Body.Children) child.Visibility = child == body ? Visibility.Visible : Visibility.Collapsed;
            Fade(Body, 10);
        }

        /// <summary>Появление как у экрана входа приложения: короткий подъём и проявление.</summary>
        static void Fade(FrameworkElement element, double shift)
        {
            element.BeginAnimation(OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromMilliseconds(300)) { EasingFunction = Ease });
            if (shift > 0 && element.RenderTransform is System.Windows.Media.TranslateTransform move)
                move.BeginAnimation(System.Windows.Media.TranslateTransform.YProperty,
                    new DoubleAnimation(shift, 0, TimeSpan.FromMilliseconds(300)) { EasingFunction = Ease });
        }

        void Breathe(bool on)
        {
            if (!on)
            {
                Mark.BeginAnimation(OpacityProperty, null);
                Mark.Opacity = 1;
                return;
            }
            Mark.BeginAnimation(OpacityProperty, new DoubleAnimation(1, 0.5, TimeSpan.FromMilliseconds(700))
            {
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever,
                EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut },
            });
        }

        void Progress(double fraction, string status)
        {
            Dispatcher.BeginInvoke(new Action(() =>
            {
                fraction = Math.Max(0, Math.Min(1, fraction));
                StatusText.Text = status;
                PercentText.Text = $"{Math.Round(fraction * 100)}%";
                var width = Track.ActualWidth * fraction;
                Fill.BeginAnimation(WidthProperty, new DoubleAnimation(width, TimeSpan.FromMilliseconds(220)) { EasingFunction = Ease });
            }));
        }

        void UpdateFolder()
        {
            FolderText.Text = Installer.ResolveTarget(options.Dir);
            FolderText.ToolTip = FolderText.Text;
            // обновление ставится туда же, где стояла прежняя версия
            ChangeButton.IsEnabled = existing == null;
            if (existing == null) options.Machine = Installer.ForAllUsers(FolderText.Text);
            // в Program Files обновлению понадобились бы права администратора: там оно идёт только новым установщиком
            var scope = !options.Machine ? "только для вас, без прав администратора"
                : Installer.IsElevated ? "для всех, без автообновления" : "для всех, нужны права, без автообновления";
            FooterText.Text = $"Версия {payload.Manifest.Version} · {Size(payload.Manifest.Size)} · {scope}";
        }

        static string Size(long bytes)
        {
            if (bytes <= 0) return "";
            var mb = bytes / 1024.0 / 1024.0;
            return mb >= 1024 ? $"{mb / 1024:0.0} ГБ" : $"{mb:0} МБ";
        }

        // ---------- действия ----------
        void OnChangeFolder(object sender, RoutedEventArgs e)
        {
            var picked = Native.PickFolder(new WindowInteropHelper(this).Handle, options.Dir);
            if (string.IsNullOrEmpty(picked)) return;
            options.Dir = picked;
            UpdateFolder();
        }

        void OnInstall(object sender, RoutedEventArgs e)
        {
            options.Desktop = DesktopBox.IsChecked == true;
            var target = Installer.ResolveTarget(options.Dir);
            if (Installer.Running(target).Count > 0)
            {
                ShowRunning(() => OnInstall(sender, e));
                return;
            }
            if (options.Machine && !Installer.IsElevated)
            {
                var args = new List<string> { "--dir", target, "--machine", "--auto-start", "--data-dir", options.DataDir };
                if (!options.Desktop) args.Add("--no-desktop");
                if (options.Smoke) args.Add("--smoke");
                Elevate(args);
                return;
            }
            RunInstall();
        }

        async void RunInstall()
        {
            busy = true;
            cancelling = false;
            cancel = new CancellationTokenSource();
            ShowProgress(existing != null ? "Обновляю Докки" : "Устанавливаю Докки");
            try
            {
                var token = cancel.Token;
                installedDir = await Task.Run(() => Installer.Install(payload, options, Progress, token));
                busy = false;
                ShowDone();
            }
            catch (OperationCanceledException)
            {
                busy = false;
                Close();
            }
            catch (Exception ex)
            {
                busy = false;
                App.Log(ex.ToString());
                ShowError(Friendly(ex), RunInstall);
            }
        }

        void OnCancel(object sender, RoutedEventArgs e)
        {
            cancelling = true;
            CancelButton.IsEnabled = false;
            StatusText.Text = "Отменяю…";
            cancel?.Cancel();
        }

        void OnFinish(object sender, RoutedEventArgs e)
        {
            if (LaunchBox.IsChecked == true && installedDir != null)
            {
                try { Installer.Launch(installedDir, Installer.LauncherExe); }
                catch (Exception ex) { App.Log(ex.ToString()); }
            }
            Close();
        }

        async void OnCloseRunning(object sender, RoutedEventArgs e)
        {
            var dir = mode == Mode.Install ? Installer.ResolveTarget(options.Dir) : options.Dir;
            busy = true;
            SetHero(Face.Mark, "Закрываю Докки", "Подождите несколько секунд");
            Switch(ProgressBody);
            CancelButton.Visibility = Visibility.Collapsed;
            StatusText.Text = "Закрываю Докки…";
            PercentText.Text = "";
            Breathe(true);
            var closed = await Task.Run(() => Installer.CloseRunning(dir));
            busy = false;
            if (closed) retry?.Invoke();
            else ShowError("Не удалось закрыть Докки. Закройте его вручную и нажмите «Повторить».", retry);
        }

        void OnRetry(object sender, RoutedEventArgs e) => retry?.Invoke();

        void OnUninstall(object sender, RoutedEventArgs e)
        {
            options.RemoveData = RemoveDataBox.IsChecked == true;
            if (Installer.Running(options.Dir).Count > 0)
            {
                ShowRunning(() => OnUninstall(sender, e));
                return;
            }
            if (Installer.NeedsElevation(options, options.Dir))
            {
                var args = new List<string> { "--uninstall", "--relocated", "--dir", options.Dir, "--auto-start" };
                if (!string.IsNullOrEmpty(options.DataDir)) args.AddRange(new[] { "--data-dir", options.DataDir });
                if (options.RemoveData) args.Add("--remove-data");
                if (options.Smoke) args.Add("--smoke");
                Elevate(args);
                return;
            }
            RunUninstall();
        }

        async void RunUninstall()
        {
            busy = true;
            ShowProgress("Удаляю Докки");
            try
            {
                await Task.Run(() => Installer.Uninstall(options, Progress));
                busy = false;
                ShowRemoved();
            }
            catch (Exception ex)
            {
                busy = false;
                App.Log(ex.ToString());
                ShowError(Friendly(ex), RunUninstall);
            }
        }

        /// <summary>Отказ в окне Windows - не ошибка: остаёмся на том же экране.</summary>
        void Elevate(List<string> args)
        {
            if (App.Elevate(args)) Close();
        }

        static string Friendly(Exception ex)
        {
            if (ex is UnauthorizedAccessException)
                return "Нет доступа к папке. Выберите другую папку или разрешите установку, когда Windows спросит.";
            if (ex is System.IO.IOException io && (io.HResult & 0xFFFF) == 112)
                return "На диске не хватает места.";
            return ex.Message;
        }

        void OnMinimize(object sender, RoutedEventArgs e) => WindowState = WindowState.Minimized;

        void OnClose(object sender, RoutedEventArgs e)
        {
            if (busy && cancel != null && !cancelling) OnCancel(sender, e);
            else if (!busy) Close();
        }

        protected override void OnClosing(CancelEventArgs e)
        {
            // посреди установки окно не закрывается молча: сначала отмена и уборка недоставленных файлов
            if (busy)
            {
                e.Cancel = true;
                if (cancel != null && !cancelling) OnCancel(this, null);
            }
            base.OnClosing(e);
        }
    }
}
