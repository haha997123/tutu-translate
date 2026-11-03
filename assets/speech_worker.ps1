param([switch]$Silent)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# All text crosses stdin as JSON. Nothing is interpolated into PowerShell code.
# This file is deliberately ASCII for Windows PowerShell 5.1 encoding safety.
try {
    Add-Type -AssemblyName System.Speech
    Add-Type -AssemblyName System.Web.Extensions
    Add-Type -ReferencedAssemblies @('System.Speech', 'System.Web.Extensions') -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Speech.Synthesis;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

public static class TutuSpeechWorker {
    private static readonly object OutputLock = new object();
    private static readonly object QueueLock = new object();
    private static readonly Queue<object> Queue = new Queue<object>();
    private static readonly AutoResetEvent Wake = new AutoResetEvent(false);
    private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    private sealed class Completed {
        public Prompt Prompt;
        public bool Cancelled;
        public Exception Error;
    }

    private static void Send(string state, object id, string message, object voices) {
        Dictionary<string, object> value = new Dictionary<string, object>();
        value["state"] = state; value["id"] = id; value["message"] = message;
        if (voices != null) value["voices"] = voices;
        lock (OutputLock) { Console.WriteLine(Json.Serialize(value)); Console.Out.Flush(); }
    }
    private static void Push(object item) {
        lock (QueueLock) Queue.Enqueue(item);
        Wake.Set();
    }
    private static object Pop() {
        lock (QueueLock) { return Queue.Count == 0 ? null : Queue.Dequeue(); }
    }
    private static void ReadCommands() {
        try {
            string line;
            // The input serializer is separate: serialization is not thread safe.
            JavaScriptSerializer parser = new JavaScriptSerializer();
            while ((line = Console.ReadLine()) != null) {
                try { Push(parser.Deserialize<Dictionary<string, object>>(line)); }
                catch { /* Reject malformed input without echoing it. */ }
            }
        } catch { }
        Dictionary<string, object> quit = new Dictionary<string, object>();
        quit["action"] = "quit"; Push(quit);
    }
    private static string GetString(Dictionary<string, object> item, string key) {
        object value; return item.TryGetValue(key, out value) ? Convert.ToString(value) : "";
    }

    public static void Run(bool silent) {
        Console.InputEncoding = new UTF8Encoding(false);
        Console.OutputEncoding = new UTF8Encoding(false);
        using (SpeechSynthesizer synth = new SpeechSynthesizer()) {
            if (silent) synth.SetOutputToNull(); else synth.SetOutputToDefaultAudioDevice();
            Dictionary<string, string> voices = new Dictionary<string, string>();
            foreach (InstalledVoice installed in synth.GetInstalledVoices()) {
                if (!installed.Enabled) continue;
                string culture = installed.VoiceInfo.Culture.TwoLetterISOLanguageName;
                if ((culture == "en" || culture == "zh") && !voices.ContainsKey(culture))
                    voices[culture] = installed.VoiceInfo.Name;
            }
            synth.SpeakCompleted += delegate(object sender, SpeakCompletedEventArgs args) {
                Push(new Completed { Prompt = args.Prompt, Cancelled = args.Cancelled, Error = args.Error });
            };
            Thread input = new Thread(ReadCommands); input.IsBackground = true; input.Start();
            Send("ready", null, "\u79bb\u7ebf\u6717\u8bfb\u5df2\u5c31\u7eea", voices);
            object currentId = null;
            Prompt currentPrompt = null;
            while (true) {
                object next = Pop();
                if (next == null) { Wake.WaitOne(250); continue; }
                Completed complete = next as Completed;
                if (complete != null) {
                    if (Object.ReferenceEquals(complete.Prompt, currentPrompt)) {
                        currentPrompt = null;
                        if (complete.Error != null)
                            Send("error", currentId, "\u6717\u8bfb\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u97f3\u9891\u8bbe\u5907\u3002", null);
                        else if (!complete.Cancelled)
                            Send("finished", currentId, "\u6717\u8bfb\u7ed3\u675f", null);
                    }
                    continue;
                }
                Dictionary<string, object> command = next as Dictionary<string, object>;
                if (command == null) continue;
                string action = GetString(command, "action");
                if (action == "quit") { synth.SpeakAsyncCancelAll(); break; }
                object id; command.TryGetValue("id", out id);
                currentId = id;
                // Forget the old Prompt before cancellation callbacks can arrive.
                currentPrompt = null;
                synth.SpeakAsyncCancelAll();
                if (action == "stop") {
                    Send("stopped", id, "\u6717\u8bfb\u5df2\u505c\u6b62", null);
                    continue;
                }
                if (action != "speak") continue;
                string language = GetString(command, "language");
                string voice;
                if (!voices.TryGetValue(language, out voice)) {
                    string missing = language == "zh" ? "\u4e2d\u6587" : "\u82f1\u6587";
                    Send("error", id, "Windows \u5c1a\u672a\u5b89\u88c5" + missing +
                         "\u8bed\u97f3\uff0c\u8bf7\u5728\u8bbe\u7f6e\u4e2d\u6dfb\u52a0\u5bf9\u5e94\u7684\u6587\u672c\u8f6c\u8bed\u97f3\u5305\u3002", null);
                    continue;
                }
                try {
                    string text = GetString(command, "text");
                    if (String.IsNullOrWhiteSpace(text) || text.Length > 10000) {
                        Send("error", id, "\u6717\u8bfb\u6587\u5b57\u65e0\u6548\u3002", null); continue;
                    }
                    synth.SelectVoice(voice);
                    object slow; command.TryGetValue("slow", out slow);
                    synth.Rate = slow is bool && (bool)slow ? -3 : 0;
                    currentPrompt = synth.SpeakAsync(text);
                    Send("speaking", id, "\u6b63\u5728\u6717\u8bfb", null);
                } catch {
                    Send("error", id, "\u6717\u8bfb\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u97f3\u9891\u8bbe\u5907\u3002", null);
                }
            }
        }
    }
}
'@
    [TutuSpeechWorker]::Run([bool]$Silent)
} catch {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    [Console]::WriteLine('{"state":"error","id":null,"message":"Windows speech component unavailable"}')
    exit 1
}
