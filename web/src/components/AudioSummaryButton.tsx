import { useEffect, useState } from 'react';
import { Volume2, VolumeX } from 'lucide-react';

interface AudioSummaryButtonProps {
  summaryText: string;
  label?: string;
}

export function AudioSummaryButton({
  summaryText,
  label = 'Audio Summary',
}: AudioSummaryButtonProps) {
  const [speaking, setSpeaking] = useState(false);
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    return () => {
      if (typeof window !== 'undefined' && 'speechSynthesis' in window && window.speechSynthesis) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  const handleToggleSpeech = () => {
    if (
      typeof window === 'undefined' ||
      !('speechSynthesis' in window) ||
      !window.speechSynthesis
    ) {
      setUnsupported(true);
      return;
    }

    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }

    window.speechSynthesis.cancel();
    const utterance =
      typeof SpeechSynthesisUtterance !== 'undefined'
        ? new SpeechSynthesisUtterance(summaryText)
        : ({ text: summaryText } as SpeechSynthesisUtterance);

    utterance.lang = 'en-US';
    utterance.rate = 0.98;
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);

    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  return (
    <div className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={handleToggleSpeech}
        aria-pressed={speaking}
        aria-label={speaking ? 'Stop audio summary' : `Play ${label}`}
        className="inline-flex items-center justify-center gap-2 px-4 min-h-[48px] min-w-[48px] rounded-control border-2 border-navy bg-surface text-navy font-semibold text-[15px] hover:bg-navy-light transition-colors"
      >
        {speaking ? (
          <>
            <VolumeX className="w-5 h-5 shrink-0" aria-hidden="true" />
            <span>Stop Audio</span>
          </>
        ) : (
          <>
            <Volume2 className="w-5 h-5 shrink-0" aria-hidden="true" />
            <span aria-hidden="true">🔊</span>
            <span>{label}</span>
          </>
        )}
      </button>
      {unsupported && (
        <span role="status" className="text-[12px] font-medium text-danger-text">
          Web Speech API is not supported in this browser.
        </span>
      )}
    </div>
  );
}
