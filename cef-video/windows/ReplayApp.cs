using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal sealed class ReplayWindow : Form
{
    private const string ServerUrlPrefix = "CEF_REPLAY_URL=";
    private readonly Color surface = Color.FromArgb(13, 20, 37);
    private readonly Label status;
    private readonly FlowLayoutPanel startupActions;
    private readonly TableLayoutPanel startup;
    private readonly string resources;
    private WebView2 browser;
    private Process server;
    private Uri serverUrl;
    private bool ready;
    private bool closing;
    private bool askingToClose;
    private int startupAttempt;
    private ulong navigationId;
    private Rectangle windowedBounds;
    private FormWindowState windowedState;
    private bool fullscreen;

    internal ReplayWindow()
    {
        Text = "CEF Vidéo";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(1180, 750);
        MinimumSize = new Size(600, 380);
        BackColor = surface;
        ForeColor = Color.White;
        Font = new Font("Segoe UI", 10f);
        AutoScaleMode = AutoScaleMode.Dpi;

        string bundledResources = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "replay");
        resources = Directory.Exists(bundledResources) ? bundledResources : AppDomain.CurrentDomain.BaseDirectory;

        startup = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 4, BackColor = surface };
        startup.RowStyles.Add(new RowStyle(SizeType.Percent, 50f));
        startup.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        startup.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        startup.RowStyles.Add(new RowStyle(SizeType.Percent, 50f));

        status = new Label
        {
            AutoSize = true,
            Anchor = AnchorStyles.None,
            MaximumSize = new Size(500, 0),
            TextAlign = ContentAlignment.MiddleCenter,
            Padding = new Padding(12),
            ForeColor = Color.White
        };
        startupActions = new FlowLayoutPanel { AutoSize = true, Anchor = AnchorStyles.None, WrapContents = false };
        var retry = new Button { Text = "Redémarrer", AutoSize = true, MinimumSize = new Size(105, 32) };
        var quit = new Button { Text = "Quitter", AutoSize = true, MinimumSize = new Size(105, 32) };
        retry.Click += async delegate { await StartAsync(); };
        quit.Click += delegate { Close(); };
        startupActions.Controls.Add(retry);
        startupActions.Controls.Add(quit);
        startup.Controls.Add(status, 0, 1);
        startup.Controls.Add(startupActions, 0, 2);
        Controls.Add(startup);
        ShowStatus("Démarrage de CEF Vidéo…", false);
    }

    protected override async void OnShown(EventArgs args)
    {
        base.OnShown(args);
        await StartAsync();
    }

    private void ShowStatus(string message, bool allowRetry)
    {
        status.Text = message;
        startupActions.Visible = allowRetry;
        startup.Visible = true;
        startup.BringToFront();
    }

    private async Task StartAsync()
    {
        var attempt = ++startupAttempt;
        ready = false;
        serverUrl = null;
        navigationId = 0;
        StopServer();
        DisposeBrowser();
        ShowStatus("Démarrage de CEF Vidéo…", false);

        try
        {
            string script = Path.Combine(resources, "serveur_replay.py");
            if (!File.Exists(script))
            {
                ShowFailure(attempt, "Les fichiers de CEF Vidéo sont introuvables. Extrais l’archive complète, puis redémarre.");
                return;
            }

            string python = await Task.Run(() => FindPython());
            if (!IsCurrent(attempt))
                return;
            if (python == null)
            {
                ShowFailure(attempt, "Python 3.10 ou plus récent est introuvable. Installe Python, puis redémarre CEF Vidéo.");
                return;
            }

            var options = new CoreWebView2EnvironmentOptions { AdditionalBrowserArguments = "--autoplay-policy=no-user-gesture-required" };
            string profile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CEF Video", "WebView2");
            CoreWebView2Environment environment = await CoreWebView2Environment.CreateAsync(null, profile, options);
            if (!IsCurrent(attempt))
                return;

            browser = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = surface };
            Controls.Add(browser);
            startup.BringToFront();
            await browser.EnsureCoreWebView2Async(environment);
            if (!IsCurrent(attempt))
                return;

            ConfigureBrowser(attempt);
            Uri origin = await StartServerAsync(python, script, attempt);
            if (!IsCurrent(attempt))
                return;
            if (origin == null)
            {
                ShowFailure(attempt, "Le serveur local ne répond pas. Vérifie les fichiers du lecteur, puis redémarre.");
                return;
            }
            serverUrl = origin;
            browser.CoreWebView2.Navigate(origin.AbsoluteUri);
        }
        catch (WebView2RuntimeNotFoundException)
        {
            ShowFailure(attempt, "Microsoft Edge WebView2 Runtime est introuvable. Installe ce composant, puis redémarre CEF Vidéo.");
        }
        catch (Exception error)
        {
            Debug.WriteLine(error);
            ShowFailure(attempt, "Impossible d’ouvrir le lecteur local. Vérifie l’installation de CEF Vidéo, puis redémarre.");
        }
    }

    private void ConfigureBrowser(int attempt)
    {
        CoreWebView2 core = browser.CoreWebView2;
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.AreDefaultScriptDialogsEnabled = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.AreBrowserAcceleratorKeysEnabled = false;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.Settings.IsZoomControlEnabled = false;

        core.NavigationStarting += (sender, args) =>
        {
            args.Cancel = !IsLocal(args.Uri);
            if (!args.Cancel)
                navigationId = args.NavigationId;
        };
        core.FrameNavigationStarting += (sender, args) => args.Cancel = true;
        core.NewWindowRequested += (sender, args) => args.Handled = true;
        core.NavigationCompleted += (sender, args) =>
        {
            if (!IsCurrent(attempt) || args.NavigationId != navigationId)
                return;
            if (!args.IsSuccess)
            {
                PostToWindow(() => ShowFailure(attempt, "Impossible de charger le lecteur. Redémarre CEF Vidéo."));
                return;
            }
            ready = true;
            startup.Visible = false;
            browser.BringToFront();
            browser.Focus();
        };
        core.ContainsFullScreenElementChanged += (sender, args) => SetFullscreen(core.ContainsFullScreenElement);
        core.ProcessFailed += (sender, args) =>
        {
            if (args.ProcessFailedKind == CoreWebView2ProcessFailedKind.BrowserProcessExited
                || args.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessExited)
                PostToWindow(() => ShowFailure(attempt, "Le lecteur s’est arrêté. Redémarre CEF Vidéo."));
        };
        core.WebMessageReceived += (sender, args) =>
        {
            if (!IsCurrent(attempt) || !IsLocal(args.Source))
                return;
            if (args.WebMessageAsJson == "\"quit\"")
                PostToWindow(CloseFromPlayer);
        };
    }

    private bool IsCurrent(int attempt)
    {
        return !closing && attempt == startupAttempt && !IsDisposed;
    }

    private bool IsLocal(string source)
    {
        Uri uri;
        return serverUrl != null && Uri.TryCreate(source, UriKind.Absolute, out uri)
            && uri.Scheme == "http" && uri.Host == "127.0.0.1" && uri.UserInfo.Length == 0 && uri.Port == serverUrl.Port;
    }

    private static ProcessStartInfo PythonProcess(string executable, string arguments, string directory)
    {
        var info = new ProcessStartInfo(executable, arguments)
        {
            WorkingDirectory = directory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8
        };
        info.EnvironmentVariables["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false";
        return info;
    }

    private static string FindPython()
    {
        const string probe = "-X utf8 -c \"import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)\"";
        foreach (string executable in new[] { "py.exe", "python.exe" })
        {
            string arguments = executable == "py.exe" ? "-3 " + probe : probe;
            try
            {
                using (var process = Process.Start(PythonProcess(executable, arguments, Environment.SystemDirectory)))
                {
                    Task<string> output = process.StandardOutput.ReadToEndAsync();
                    process.BeginErrorReadLine();
                    if (!process.WaitForExit(4000))
                    {
                        StopProcess(process);
                        continue;
                    }
                    string path = output.GetAwaiter().GetResult().Trim();
                    if (process.ExitCode == 0 && Path.IsPathRooted(path) && File.Exists(path))
                        return path;
                }
            }
            catch (Win32Exception)
            {
                // The launcher or interpreter may not be installed.
            }
        }
        return null;
    }

    private async Task<Uri> StartServerAsync(string python, string script, int attempt)
    {
        var process = new Process
        {
            StartInfo = PythonProcess(python, "-B -u -X utf8 \"" + script + "\" --port 0 --sans-navigateur", resources),
            EnableRaisingEvents = true
        };
        var started = new TaskCompletionSource<Uri>(TaskCreationOptions.RunContinuationsAsynchronously);
        process.OutputDataReceived += (sender, args) =>
        {
            string line = args.Data;
            Uri origin;
            if (line != null && line.StartsWith(ServerUrlPrefix, StringComparison.Ordinal)
                && Uri.TryCreate(line.Substring(ServerUrlPrefix.Length), UriKind.Absolute, out origin)
                && origin.Scheme == "http" && origin.Host == "127.0.0.1" && origin.UserInfo.Length == 0
                && origin.Port > 0 && origin.Port != 80 && origin.PathAndQuery == "/" && origin.Fragment.Length == 0)
                started.TrySetResult(origin);
        };
        process.Exited += (sender, args) =>
        {
            started.TrySetResult(null);
            PostToWindow(() =>
            {
                if (server == process)
                    ShowFailure(attempt, "Le serveur local s’est arrêté. Redémarre CEF Vidéo.");
            });
        };
        server = process;
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        return await Task.WhenAny(started.Task, Task.Delay(TimeSpan.FromSeconds(15))) == started.Task
            ? await started.Task : null;
    }

    private void PostToWindow(Action work)
    {
        if (!IsHandleCreated || IsDisposed)
            return;
        try { BeginInvoke(work); }
        catch (InvalidOperationException) { }
    }

    private void ShowFailure(int attempt, string message)
    {
        if (!IsCurrent(attempt))
            return;
        startupAttempt++;
        ready = false;
        serverUrl = null;
        StopServer();
        DisposeBrowser();
        ShowStatus(message, true);
    }

    private void DisposeBrowser()
    {
        if (browser == null)
            return;
        SetFullscreen(false);
        Controls.Remove(browser);
        browser.Dispose();
        browser = null;
    }

    private static void StopProcess(Process child)
    {
        try
        {
            if (!child.HasExited)
                child.Kill();
        }
        catch (InvalidOperationException) { }
        catch (Win32Exception) { }
    }

    private void StopServer()
    {
        Process child = server;
        server = null;
        if (child == null)
            return;
        StopProcess(child);
        child.Dispose();
    }

    private void SetFullscreen(bool enabled)
    {
        if (fullscreen == enabled)
            return;
        fullscreen = enabled;
        if (enabled)
        {
            Rectangle monitor = Screen.FromControl(this).Bounds;
            windowedState = WindowState;
            windowedBounds = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
            WindowState = FormWindowState.Normal;
            FormBorderStyle = FormBorderStyle.None;
            Bounds = monitor;
            TopMost = true;
        }
        else
        {
            TopMost = false;
            FormBorderStyle = FormBorderStyle.Sizable;
            Bounds = windowedBounds;
            WindowState = windowedState;
        }
    }

    protected override void OnFormClosing(FormClosingEventArgs args)
    {
        if (!closing && ready && args.CloseReason != CloseReason.WindowsShutDown)
        {
            args.Cancel = true;
            if (!askingToClose)
                AskPlayerToClose();
        }
        base.OnFormClosing(args);
    }

    private async void AskPlayerToClose()
    {
        askingToClose = true;
        try
        {
            if (browser == null || browser.Source == null || !IsLocal(browser.Source.AbsoluteUri)
                || await browser.ExecuteScriptAsync("typeof window.demanderDeQuitter === 'function' && (window.demanderDeQuitter(), true)") != "true")
                AskToCloseUnavailablePlayer();
        }
        catch (Exception error)
        {
            Debug.WriteLine(error);
            if (!closing)
                AskToCloseUnavailablePlayer();
        }
        finally
        {
            askingToClose = false;
        }
    }

    private void AskToCloseUnavailablePlayer()
    {
        PostToWindow(() =>
        {
            if (!closing && MessageBox.Show(this, "Le lecteur ne répond pas. Fermer CEF Vidéo ?", Text,
                MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) == DialogResult.Yes)
                CloseFromPlayer();
        });
    }

    private void CloseFromPlayer()
    {
        closing = true;
        Close();
    }

    protected override void OnFormClosed(FormClosedEventArgs args)
    {
        closing = true;
        startupAttempt++;
        StopServer();
        base.OnFormClosed(args);
    }

    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new ReplayWindow());
    }
}
