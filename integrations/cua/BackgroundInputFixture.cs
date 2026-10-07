using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

// Independent test application and read-only foreground/cursor observer.
// It contains no injected input and no UI Automation client.
class BackgroundInputFixture
{
    [StructLayout(LayoutKind.Sequential)] struct Point { public int X, Y; }
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool GetCursorPos(out Point point);
    delegate void WinEventProc(IntPtr hook, uint evt, IntPtr window, int obj, int child, uint thread, uint time);
    [DllImport("user32.dll")] static extern IntPtr SetWinEventHook(uint min, uint max, IntPtr module, WinEventProc callback, uint process, uint thread, uint flags);
    [DllImport("user32.dll")] static extern bool UnhookWinEvent(IntPtr hook);
    static readonly List<long> ForegroundEvents = new List<long>();
    static WinEventProc Callback;
    static int Clicks;
    static long EventForeground;
    static Point EventCursor;
    static TextBox Editor;
    static readonly string StatePath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "verification", "fixture-state.json");
    static string Quote(string text) { return "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n").Replace("\t", "\\t") + "\""; }
    static void Save()
    {
        Point p; GetCursorPos(out p);
        var events = String.Join(",", ForegroundEvents);
        string json = "{\"clicks\":" + Clicks + ",\"text\":" + Quote(Editor.Text) + ",\"foreground\":" + GetForegroundWindow().ToInt64() +
            ",\"cursor\":[" + p.X + "," + p.Y + "],\"foreground_events\":[" + events + "],\"click_foreground\":" + EventForeground +
            ",\"click_cursor\":[" + EventCursor.X + "," + EventCursor.Y + "]}";
        try { Directory.CreateDirectory(Path.GetDirectoryName(StatePath)); File.WriteAllText(StatePath, json, new UTF8Encoding(false)); } catch (IOException) { }
    }
    [STAThread] static void Main()
    {
        var app = new Application();
        var panel = new StackPanel { Margin = new Thickness(20) };
        var label = new TextBlock { Text = "Independent background test: clicks = 0", Margin = new Thickness(0,0,0,16) };
        var button = new Button { Content = "Background Test Increment", Height = 42, Margin = new Thickness(0,0,0,16) };
        Editor = new TextBox { Text = "", Height = 34 };
        button.Click += delegate { Clicks++; EventForeground = GetForegroundWindow().ToInt64(); GetCursorPos(out EventCursor); label.Text = "Independent background test: clicks = " + Clicks; Save(); };
        Editor.TextChanged += delegate { Save(); };
        panel.Children.Add(label); panel.Children.Add(button); panel.Children.Add(Editor);
        var window = new Window { Title = "Cua Background Test", Width = 460, Height = 220, Left = 40, Top = 80, ShowActivated = false, Content = panel };
        Callback = delegate(IntPtr h, uint e, IntPtr w, int o, int c, uint t, uint ms) { ForegroundEvents.Add(w.ToInt64()); };
        var hook = SetWinEventHook(3, 3, IntPtr.Zero, Callback, 0, 0, 0);
        var timer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(100) };
        timer.Tick += delegate { Save(); }; timer.Start();
        window.Closed += delegate { timer.Stop(); UnhookWinEvent(hook); };
        Save(); app.Run(window);
    }
}
