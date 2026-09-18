from services.onboarding_service import OnboardingStore, OnboardingStep, TelegramOnboardingService


def test_onboarding_resumes_without_duplicate_state(tmp_path):
    service = TelegramOnboardingService(OnboardingStore(tmp_path / 'onboarding.json'))
    assert service.start()['step'] == 'timezone'
    assert service.set_timezone('Asia/Tomsk')['step'] == 'source'
    restarted = TelegramOnboardingService(OnboardingStore(tmp_path / 'onboarding.json'))
    assert restarted.start()['step'] == 'source'
    restarted.source_connected('tpu-8i41'); restarted.attendance_completed(); restarted.profile_completed()
    assert restarted.calendar_checked()['step'] == OnboardingStep.COMPLETE.value
