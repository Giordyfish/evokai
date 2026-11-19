/**
 * React entry point: thin hooks over `/browser`. No components, no styles: the application draws its own interface.
 */
export {
  EvokaiProvider,
  useEvokaiContext,
  useEvokaiSession,
  usePhase,
  useUsage,
  useVoice,
  useMicrophone,
  useAudioSignals,
  useDictation,
} from './context'
export type { EvokaiContextValue, EvokaiProviderProps } from './context'
