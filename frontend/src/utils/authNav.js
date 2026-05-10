/**
 * Post-login navigation — single source of truth for where to send a user
 * after any login event (Google, OTP, refresh, app restart).
 *
 * Rules (in priority order):
 *  1. Admin phone          → /admin
 *  2. No name or role      → /onboarding  (step 1: name + role)
 *  3. Recruiter            → recruiter onboarding or dashboard
 *  4. Profile incomplete   → /onboarding  (step 2: professional details)
 *  5. Everything complete  → /dashboard
 */
export function getPostLoginRoute(userData) {
  const adminPhone = import.meta.env.VITE_ADMIN_PHONE;
  if (adminPhone && userData.phone === adminPhone) return '/admin';
  if (!userData.name || !userData.role) return '/onboarding';
  if (userData.role === 'recruiter') {
    return userData.company_name ? '/recruiter/dashboard' : '/recruiter/onboarding';
  }
  if (!userData.profile_complete) return '/onboarding';
  return '/dashboard';
}

/** Call after a successful login to navigate to the correct screen. */
export function navigateAfterLogin(userData, navigate) {
  // Restore a pending deep link saved by AppLinkHandler when user was logged out
  const pendingDeepLink = sessionStorage.getItem('mediroute_deep_link');
  if (pendingDeepLink) {
    sessionStorage.removeItem('mediroute_deep_link');
    console.log('[MediRoute][DeepLink] restoring post-login deep link:', pendingDeepLink);
    navigate(pendingDeepLink, { replace: true });
    return;
  }
  navigate(getPostLoginRoute(userData), { replace: true });
}
