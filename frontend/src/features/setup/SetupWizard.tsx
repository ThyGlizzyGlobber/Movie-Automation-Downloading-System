import { useMemo, useState } from 'react'
import { useSetupStatus } from './useSetupStatus'
import SetupTokenStep from './SetupTokenStep'
import TmdbStep from './TmdbStep'
import QbittorrentStep from './QbittorrentStep'
import PlexLinkStep from './PlexLinkStep'
import LoadingState from '../../components/LoadingState'
import ErrorState from '../../components/ErrorState'
import './SetupWizard.css'
import AmbientGlow from '../../components/AmbientGlow'

type WizardStep = 'token' | 'tmdb' | 'qbittorrent' | 'plex'

export default function SetupWizard() {
  const setupStatus = useSetupStatus()
  const [setupToken, setSetupToken] = useState<string | null>(null)
  const [restartNeeded, setRestartNeeded] = useState(false)

  // Only the steps that still need doing — an env-configured value is
  // never re-collected through the UI (the backend would 409 anyway).
  // Neither step is skippable otherwise: both TMDB and qBittorrent are
  // required for the app to actually do anything.
  const steps = useMemo<WizardStep[]>(() => {
    const list: WizardStep[] = ['token']
    if (setupStatus.data?.tmdb_source !== 'env') list.push('tmdb')
    if (setupStatus.data?.qbt_source !== 'env') list.push('qbittorrent')
    list.push('plex')
    return list
  }, [setupStatus.data])
  const [stepIndex, setStepIndex] = useState(0)
  const step = steps[Math.min(stepIndex, steps.length - 1)]

  function advance() {
    setStepIndex((i) => Math.min(i + 1, steps.length - 1))
  }

  if (setupStatus.isLoading) return <LoadingState />
  if (setupStatus.isError) {
    return <ErrorState message={setupStatus.error instanceof Error ? setupStatus.error.message : undefined} />
  }

  return (
    <div className="setup-page">
      <AmbientGlow posterPath={null} />
      <div className="setup-card">
        <div className="setup-progress">
          {steps.map((s, i) => (
            <div key={s} className={`setup-progress-dot${i < stepIndex ? ' done' : i === stepIndex ? ' active' : ''}`} />
          ))}
        </div>

        {step === 'token' && (
          <SetupTokenStep
            onContinue={(token) => {
              setSetupToken(token)
              advance()
            }}
          />
        )}

        {step === 'tmdb' && setupToken && (
          <TmdbStep
            setupToken={setupToken}
            onDone={() => {
              setRestartNeeded(true)
              advance()
            }}
          />
        )}

        {step === 'qbittorrent' && setupToken && (
          <QbittorrentStep
            setupToken={setupToken}
            onDone={() => {
              setRestartNeeded(true)
              advance()
            }}
          />
        )}

        {step === 'plex' && setupToken && <PlexLinkStep setupToken={setupToken} />}

        {restartNeeded && (
          <p className="setup-skip-note">
            Saved. Restart Meridian once you've finished so it picks this up.
          </p>
        )}
      </div>
    </div>
  )
}
