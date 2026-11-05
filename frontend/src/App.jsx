import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { BrowserRouter, Link, NavLink, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowDownToLine, ArrowLeft, ArrowRight, Bell, BookOpen, Check, CheckCircle2,
  ChevronDown, ChevronLeft, ChevronRight, Copy, Eye, EyeOff, FileText, Folder,
  GraduationCap, House, LockKeyhole, LogOut, Mail, Maximize, Menu, Minimize, Plus, Search, ShieldCheck,
  Trash2, Upload, UserRound, X, ZoomIn, ZoomOut,
} from 'lucide-react';

import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

const AuthContext = createContext(null);
const FlashContext = createContext(() => {});
const sectionTones = ['blue', 'violet', 'green', 'orange', 'pink'];

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const method = (options.method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-Requested-With', 'StudyHub');
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, { ...options, method, headers, credentials: 'same-origin' });
  if (response.status === 204) return null;
  const type = response.headers.get('content-type') || '';
  const payload = type.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status}).`);
  return payload;
}

function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [downloadsEnabled, setDownloadsEnabled] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api('/api/auth/session')
      .then((result) => { setUser(result.user); setDownloadsEnabled(result.downloads_enabled); })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const signIn = async (userId, password, rememberMe) => {
    const result = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ user_id: userId, password, remember_me: rememberMe }) });
    setUser(result.user);
    setDownloadsEnabled(result.downloads_enabled);
    return result.user;
  };

  const signOut = async () => {
    try { await api('/api/auth/logout', { method: 'POST' }); } finally { setUser(null); }
  };

  return <AuthContext.Provider value={{ user, setUser, downloadsEnabled, setDownloadsEnabled, loading, signIn, signOut }}>{children}</AuthContext.Provider>;
}

function useAuth() { return useContext(AuthContext); }
function useFlash() { return useContext(FlashContext); }

function App() {
  const [toast, setToast] = useState(null);
  const timer = useRef(null);
  const flash = useCallback((message, kind = 'success') => {
    setToast({ message, kind });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 3600);
  }, []);

  return (
    <BrowserRouter>
      <AuthProvider>
        <FlashContext.Provider value={flash}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/" element={<Gate><HomePage /></Gate>} />
            <Route path="/section/:sectionId" element={<Gate><SectionPage /></Gate>} />
            <Route path="/pdf/:pdfId" element={<Gate><PdfViewerPage /></Gate>} />
            <Route path="/admin" element={<Gate admin><AdminPage /></Gate>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          {toast && <div className={`toast toast-${toast.kind}`} role="status"><CheckCircle2 size={17} />{toast.message}</div>}
        </FlashContext.Provider>
      </AuthProvider>
    </BrowserRouter>
  );
}

function Gate({ children, admin = false }) {
  const { user, loading } = useAuth();
  useEffect(() => {
    const suppress = (event) => event.preventDefault();
    document.addEventListener('contextmenu', suppress);
    document.addEventListener('dragstart', suppress);
    return () => { document.removeEventListener('contextmenu', suppress); document.removeEventListener('dragstart', suppress); };
  }, []);
  if (loading) return <div className="full-loader"><span className="spinner" /><span>Opening your study space…</span></div>;
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== 'admin') return <Navigate to="/" replace />;
  return <><SiteNav /><main className="page-shell">{children}</main></>;
}

function Brand({ light = false, compact = false }) {
  return <Link className={`brand ${light ? 'brand-light' : ''} ${compact ? 'brand-compact' : ''}`} to="/" aria-label="Krunal Study Hub home">
    <span className="brand-mark"><GraduationCap size={compact ? 27 : 36} strokeWidth={2.4} /></span>
    <span>Krunal Study Hub</span>
  </Link>;
}

function SiteNav() {
  const { user, signOut } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const navigate = useNavigate();

  const exit = async () => {
    await signOut();
    navigate('/login', { replace: true });
  };

  return <header className="topbar">
    <div className="nav-inner">
      <Brand light compact />
      <button className="mobile-menu-button" type="button" aria-label="Toggle navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}><Menu size={21} /></button>
      <nav className={`main-nav ${menuOpen ? 'nav-open' : ''}`} aria-label="Main navigation">
        <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`} onClick={() => setMenuOpen(false)}><House size={17} /> <span>Home</span></NavLink>
        {sectionTones.map((tone, index) => <NavLink key={tone} to={`/section/${index + 1}`} className={({ isActive }) => `nav-link ${isActive ? 'nav-active' : ''}`} onClick={() => setMenuOpen(false)}><Folder size={16} /> <span>Section {index + 1}</span></NavLink>)}
        {user?.role === 'admin' && <NavLink to="/admin" className={({ isActive }) => `nav-link admin-nav ${isActive ? 'nav-active' : ''}`} onClick={() => setMenuOpen(false)}><ShieldCheck size={16} /> <span>Admin</span></NavLink>}
      </nav>
      <div className="nav-actions">
        <div className="notification-wrap"><button className="icon-button bell-button" type="button" title="Notifications" aria-label="Notifications" aria-expanded={noticeOpen} onClick={() => setNoticeOpen(!noticeOpen)}><Bell size={18} /></button>{noticeOpen && <div className="notification-popover"><span className="notice-icon"><Bell size={15} /></span><b>You’re all caught up</b><small>New study materials will appear here when they’re added.</small></div>}</div>
        <div className="profile-wrap">
          <button className="profile-button" type="button" aria-expanded={profileOpen} onClick={() => setProfileOpen(!profileOpen)}>
            <span className="avatar">{(user?.id || 'KD').slice(0, 2).toUpperCase()}</span><span className="profile-id">{user?.id}</span><ChevronDown size={15} />
          </button>
          {profileOpen && <div className="profile-menu"><div className="profile-menu-user"><b>{user?.id}</b><span>{user?.role === 'admin' ? 'Administrator' : 'Viewer account'}</span></div><button type="button" onClick={exit}><LogOut size={16} /> Sign out</button></div>}
        </div>
      </div>
    </div>
  </header>;
}

function StudyIllustration() {
  return <svg className="study-illustration" viewBox="0 0 620 390" role="img" aria-label="A laptop, books, desk lamp and plant">
    <defs>
      <linearGradient id="screen" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#178cff" /><stop offset="1" stopColor="#062c74" /></linearGradient>
      <linearGradient id="bookBlue" x1="0" x2="1"><stop stopColor="#0874ff" /><stop offset="1" stopColor="#20b9ff" /></linearGradient>
      <linearGradient id="lampGold" x1="0" x2="0" y2="1"><stop stopColor="#fff5c9" /><stop offset="1" stopColor="#ffc86a" /></linearGradient>
      <radialGradient id="glow"><stop stopColor="#2778ff" stopOpacity=".68" /><stop offset="1" stopColor="#0b3987" stopOpacity="0" /></radialGradient>
      <filter id="shadow" x="-.2" y="-.2" width="1.4" height="1.6"><feGaussianBlur stdDeviation="10" /></filter>
    </defs>
    <ellipse cx="333" cy="302" rx="256" ry="38" fill="#000c23" opacity=".5" filter="url(#shadow)" />
    <ellipse cx="330" cy="203" rx="285" ry="180" fill="url(#glow)" />
    <path d="M65 312h503" stroke="#1d6aca" strokeWidth="3" opacity=".8" />
    <path d="M424 305l11-51m0 0 48-81m-48 81 34-87" fill="none" stroke="#152d5b" strokeWidth="12" strokeLinecap="round" />
    <circle cx="435" cy="254" r="13" fill="#2d62bd" />
    <path d="M462 166c-31-14-48-33-51-55 31-6 61 3 84 29l-33 26Z" fill="#1852a9" stroke="#4989fa" strokeWidth="4" />
    <path d="M399 110c5-16 20-27 39-29l23 46-23 15c-20-5-34-16-39-32Z" fill="url(#lampGold)" />
    <path d="M400 111c15 13 35 21 59 19" fill="none" stroke="#fff7db" strokeWidth="5" opacity=".9" />
    <path d="M388 306v-38m0 0c-24-44-13-69 4-97m-4 72c-30-28-42-50-40-72m46 65c20-30 29-51 27-73" fill="none" stroke="#2acb87" strokeWidth="7" strokeLinecap="round" />
    <ellipse cx="349" cy="196" rx="13" ry="31" transform="rotate(-42 349 196)" fill="#28c783" />
    <ellipse cx="377" cy="174" rx="12" ry="30" transform="rotate(-29 377 174)" fill="#5decac" />
    <ellipse cx="416" cy="167" rx="12" ry="29" transform="rotate(17 416 167)" fill="#28c783" />
    <ellipse cx="401" cy="210" rx="12" ry="29" transform="rotate(47 401 210)" fill="#20aa71" />
    <path d="M364 274h49l-8 37h-32l-9-37Z" fill="#f2f7ff" />
    <path d="M361 272h55" stroke="#d1e6ff" strokeWidth="5" />
    <rect x="125" y="262" width="119" height="21" rx="7" fill="url(#bookBlue)" />
    <rect x="133" y="251" width="122" height="18" rx="6" fill="#f6fbff" />
    <path d="M141 254h77" stroke="#c9dcf5" strokeWidth="4" />
    <rect x="113" y="280" width="140" height="21" rx="7" fill="#1552ad" />
    <path d="M130 285h91" stroke="#49a6ff" strokeWidth="4" />
    <path d="M242 181h190l-11 111H231l11-111Z" fill="#101d34" stroke="#6485b9" strokeWidth="5" />
    <path d="M250 190h172l-9 91H241l9-91Z" fill="url(#screen)" />
    <path d="m333 218 32 19-32 18-32-18 32-19Zm-20 31v17l20 12 20-12v-17l-20 11-20-11Zm47-7v19" fill="#e6f4ff" stroke="#e6f4ff" strokeLinejoin="round" strokeWidth="4" />
    <path d="M229 290h211l19 12c2 6-4 10-12 10H224c-10 0-13-5-10-10l15-12Z" fill="#b8cbe2" />
    <path d="M304 296h62" stroke="#728cab" strokeWidth="4" strokeLinecap="round" />
    <path d="M265 245v39" stroke="#fff" strokeWidth="8" strokeLinecap="round" />
    <path d="M260 240h10l7-33" stroke="#dceeff" strokeWidth="7" strokeLinecap="round" />
    <rect x="254" y="205" width="25" height="20" rx="5" fill="#f4f8ff" />
    <path d="m258 204 5-22h13l4 22" fill="#68b4ff" />
    <path d="M0 0h620v390H0z" fill="none" />
  </svg>;
}

function LoginPage() {
  const { user, loading, signIn } = useAuth();
  const navigate = useNavigate();
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { if (!loading && user) navigate('/', { replace: true }); }, [loading, user, navigate]);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true); setError('');
    try { await signIn(userId.trim(), password, remember); navigate('/', { replace: true }); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return <div className="login-screen">
    <section className="login-story">
      <Brand light />
      <div className="login-pitch"><p className="eyebrow">YOUR PERSONAL STUDY SPACE</p><h1>Study<br />Organize<br />Learn<br /><span>In One Place</span></h1>
        <ul className="benefit-list"><li><Check size={15} /> Access study PDFs</li><li><Check size={15} /> 5 organized sections</li><li><Check size={15} /> Simple &amp; clean interface</li></ul>
      </div>
      <div className="login-illustration-wrap"><StudyIllustration /></div>
      <div className="story-glow" />
    </section>
    <section className="login-form-side">
      <div className="login-form-card">
        <div className="form-heading"><div className="form-cap"><GraduationCap size={27} /></div><h2>Welcome Back <span aria-hidden="true">👋</span></h2><p>Sign in to access your study materials</p></div>
        <form onSubmit={submit}>
          <label className="field-wrap"><Mail size={20} /><input autoComplete="username" name="user_id" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="Email or Username" aria-label="Email or Username" required /></label>
          <label className="field-wrap"><LockKeyhole size={20} /><input autoComplete="current-password" name="password" type={visible ? 'text' : 'password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" aria-label="Password" required /><button className="field-action" type="button" aria-label={visible ? 'Hide password' : 'Show password'} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={19} /> : <Eye size={19} />}</button></label>
          <label className="remember-row"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} /><span className="custom-check"><Check size={13} /></span><span>Remember me</span></label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="primary-button sign-in-button" type="submit" disabled={busy}>{busy ? <><span className="button-spinner" /> Signing in…</> : <>Sign In <ArrowRight size={21} /></>}</button>
        </form>
      </div>
      <p className="login-footnote"><ShieldCheck size={14} /> Private study space for invited accounts</p>
    </section>
  </div>;
}

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value) {
  return new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' }).format(new Date(value));
}

function SectionFolder({ section, large = false }) {
  return <span className={`folder-icon ${section.tone || sectionTones[(section.id - 1) % sectionTones.length]} ${large ? 'folder-large' : ''}`}><Folder size={large ? 42 : 39} strokeWidth={1.8} fill="currentColor" /></span>;
}

function HomePage() {
  const [sections, setSections] = useState([]);
  const [search, setSearch] = useState('');
  const [results, setResults] = useState([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [busy, setBusy] = useState(true);
  const searchBox = useRef(null);
  const searchInput = useRef(null);
  const { user, setDownloadsEnabled } = useAuth();
  const flash = useFlash();

  useEffect(() => { api('/api/home').then((data) => { setSections(data.sections); setDownloadsEnabled(data.downloads_enabled); }).catch((error) => flash(error.message, 'error')).finally(() => setBusy(false)); }, [flash, setDownloadsEnabled]);
  useEffect(() => {
    if (!search.trim()) { setResults([]); return undefined; }
    const timer = setTimeout(() => api(`/api/search?q=${encodeURIComponent(search)}`).then((data) => setResults(data.files)).catch(() => setResults([])), 240);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const close = (event) => { if (searchBox.current && !searchBox.current.contains(event.target)) setSearchOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  useEffect(() => {
    const shortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchInput.current?.focus();
        setSearchOpen(true);
      }
      if (event.key === 'Escape') setSearchOpen(false);
    };
    document.addEventListener('keydown', shortcut);
    return () => document.removeEventListener('keydown', shortcut);
  }, []);

  return <>
    <section className="home-hero">
      <div className="hero-copy"><p className="eyebrow hero-eyebrow">YOUR LEARNING, ORGANIZED</p><h1>Welcome to<br /><span className="hero-name">Krunal <em>Dodiya’s</em></span><br />Study Hub</h1>
        <p className="hero-description">Your personal place to study, organize and access all important study materials (PDFs) in one place.</p>
        <div className="global-search-wrap" ref={searchBox}><label className="global-search"><Search size={20} /><input ref={searchInput} value={search} onFocus={() => setSearchOpen(true)} onChange={(event) => { setSearch(event.target.value); setSearchOpen(true); }} placeholder="Search PDFs, notes..." aria-label="Search PDFs and notes" /><kbd>Ctrl K</kbd></label>
          {searchOpen && search.trim() && <div className="search-results">{results.length ? results.map((file) => <Link key={file.id} to={`/pdf/${file.id}`} onClick={() => setSearchOpen(false)}><FileText size={17} /><span><b>{file.filename}</b><small>{file.section_name}</small></span><ArrowRight size={15} /></Link>) : <p className="search-empty">No matching study material found.</p>}</div>}
        </div>
        <div className="hero-assurance"><span><ShieldCheck size={15} /> Private to your account</span><span><BookOpen size={15} /> Five organized sections</span></div>
      </div>
      <div className="hero-art"><div className="hero-orbit" /><StudyIllustration /><div className="art-caption"><span className="art-caption-icon"><BookOpen size={16} /></span><span><b>Ready when you are</b><small>Your study space, all in one place</small></span><span className="caption-sparkle">✦</span></div></div>
      <div className="hero-wave" />
    </section>
    <section className="section-overview" aria-labelledby="sections-heading">
      <div className="section-intro"><div><p className="eyebrow">PICK UP WHERE YOU LEFT OFF</p><h2 id="sections-heading">Your study sections</h2><p>Everything you need, neatly organized.</p></div><div className="section-count"><span>{busy ? '—' : sections.reduce((sum, item) => sum + item.count, 0)}</span><small>PDFs available</small></div></div>
      {busy ? <div className="section-loading"><span className="spinner" /> Loading your sections…</div> : <div className="folder-grid">{sections.map((section) => <article key={section.id} className={`folder-card tone-${section.tone}`}>
        <Link className="folder-card-main" to={`/section/${section.id}`} aria-label={`Open ${section.name}`}><div className="folder-card-top"><SectionFolder section={section} /><span className="card-open"><ArrowRight size={17} /></span></div><h3>{section.name}</h3><span className="pdf-count-pill"><FileText size={13} /> {section.count} {section.count === 1 ? 'PDF' : 'PDFs'}</span><p>{section.description}</p></Link>
        <Link className="folder-view-link" to={`/section/${section.id}`}>View PDFs <ArrowRight size={16} /></Link>
      </article>)}</div>}
      {user?.role === 'admin' && <div className="admin-home-strip"><div className="admin-strip-icon"><ShieldCheck size={19} /></div><span><b>Administrator tools</b><small>Manage study PDFs and viewer accounts</small></span><Link to="/admin">Open dashboard <ArrowRight size={16} /></Link></div>}
    </section>
    <footer className="site-footer"><Brand compact /><span>A thoughtful place to learn, one page at a time.</span><small>© {new Date().getFullYear()} Krunal Study Hub</small></footer>
  </>;
}

function UploadControl({ section, onUploaded }) {
  const { user } = useAuth();
  const flash = useFlash();
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  if (user?.role !== 'admin') return null;
  const upload = async (file) => {
    if (!file) return;
    const body = new FormData(); body.append('section', String(section)); body.append('file', file);
    setBusy(true);
    try { await api('/api/admin/pdfs', { method: 'POST', body }); flash('PDF uploaded successfully.'); onUploaded(); }
    catch (error) { flash(error.message, 'error'); }
    finally { setBusy(false); if (inputRef.current) inputRef.current.value = ''; }
  };
  return <><input ref={inputRef} hidden type="file" accept="application/pdf,.pdf" onChange={(event) => upload(event.target.files?.[0])} /><button className="primary-button upload-button" type="button" onClick={() => inputRef.current?.click()} disabled={busy}>{busy ? <span className="button-spinner" /> : <Upload size={17} />}{busy ? 'Uploading…' : 'Upload PDF'}</button></>;
}

function SectionPage() {
  const { sectionId } = useParams();
  const sectionNumber = Number(sectionId);
  const { user, downloadsEnabled, setDownloadsEnabled } = useAuth();
  const flash = useFlash();
  const [section, setSection] = useState(null);
  const [files, setFiles] = useState([]);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const fileInputs = useRef({});

  useEffect(() => {
    let live = true;
    setBusy(true);
    api(`/api/sections/${sectionNumber}/pdfs?search=${encodeURIComponent(search)}`)
      .then((data) => { if (live) { setSection(data.section); setFiles(data.files); setDownloadsEnabled(data.downloads_enabled); } })
      .catch((error) => { if (live) flash(error.message, 'error'); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [sectionNumber, search, refreshKey, flash, setDownloadsEnabled]);

  const refresh = () => setRefreshKey((value) => value + 1);
  const replaceFile = async (pdfId, file) => {
    if (!file) return;
    const body = new FormData(); body.append('file', file);
    try { await api(`/api/admin/pdfs/${pdfId}`, { method: 'PUT', body }); flash('PDF replaced.'); refresh(); }
    catch (error) { flash(error.message, 'error'); }
  };
  const removeFile = async (file) => {
    if (!window.confirm(`Delete “${file.filename}” from ${section?.name}?`)) return;
    try { await api(`/api/admin/pdfs/${file.id}`, { method: 'DELETE' }); flash('PDF deleted.'); refresh(); }
    catch (error) { flash(error.message, 'error'); }
  };

  if (!section && !busy) return <div className="not-found"><h2>Section not found</h2><Link to="/">Return home</Link></div>;
  return <section className="section-page">
    <div className="breadcrumb-row"><Link to="/" className="back-link"><ArrowLeft size={16} /> All sections</Link><span className="breadcrumb-separator">/</span><span>{section?.name || `Section ${sectionNumber}`}</span></div>
    <div className="section-page-heading"><div className="section-title-group"><SectionFolder section={section || { id: sectionNumber, tone: sectionTones[(sectionNumber - 1) % 5] }} large /><div><p className="eyebrow">YOUR STUDY LIBRARY</p><h1>{section?.name || `Section ${sectionNumber}`}</h1><p>{section?.description || 'All study materials and PDFs in this section.'}</p></div></div><UploadControl section={sectionNumber} onUploaded={refresh} /></div>
    <div className="file-card"><div className="file-card-toolbar"><div className="file-total"><FileText size={18} /><strong>{busy ? '…' : `${files.length} ${files.length === 1 ? 'PDF' : 'PDFs'}`}</strong></div><label className="table-search"><Search size={17} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={`Search in ${section?.name || 'section'}…`} aria-label="Search PDFs in this section" />{search && <button type="button" aria-label="Clear search" onClick={() => setSearch('')}><X size={15} /></button>}</label></div>
      <div className="table-scroll"><table className="pdf-table"><thead><tr><th className="col-index">#</th><th>File Name</th><th className="col-size">Size</th><th className="col-date">Uploaded On</th><th className="col-actions">Actions</th></tr></thead><tbody>
        {busy && <tr><td colSpan="5" className="table-message"><span className="spinner" /> Loading PDFs…</td></tr>}
        {!busy && files.map((file, index) => <tr key={file.id}><td className="file-index">{index + 1}</td><td><div className="file-name-cell"><span className="pdf-file-icon"><FileText size={17} /></span><span>{file.filename}</span></div></td><td>{formatSize(file.size_bytes)}</td><td>{formatDate(file.uploaded_at)}</td><td><div className="table-actions"><Link className="action-button action-view" to={`/pdf/${file.id}`} title="View PDF" aria-label={`View ${file.filename}`}><Eye size={16} /></Link>{(downloadsEnabled || user.role === 'admin') && <a className="action-button action-download" href={`/api/pdfs/${file.id}/content?download=true`} title="Download PDF" aria-label={`Download ${file.filename}`}><ArrowDownToLine size={16} /></a>}{user.role === 'admin' && <><input ref={(node) => { fileInputs.current[file.id] = node; }} hidden type="file" accept="application/pdf,.pdf" onChange={(event) => { replaceFile(file.id, event.target.files?.[0]); event.target.value = ''; }} /><button className="action-button action-replace" type="button" title="Replace PDF" aria-label={`Replace ${file.filename}`} onClick={() => fileInputs.current[file.id]?.click()}><Upload size={15} /></button><button className="action-button action-delete" type="button" title="Delete PDF" aria-label={`Delete ${file.filename}`} onClick={() => removeFile(file)}><Trash2 size={15} /></button></>}</div></td></tr>)}
        {!busy && files.length === 0 && <tr><td colSpan="5"><div className="empty-table"><span className="empty-file-icon"><FileText size={23} /></span><b>{search ? 'No matching PDFs' : 'No study PDFs here yet'}</b><p>{search ? 'Try a different search term.' : user.role === 'admin' ? 'Upload your first PDF to make it available to viewers.' : 'New study material will appear here when it is added.'}</p>{search && <button className="text-button" onClick={() => setSearch('')}>Clear search</button>}</div></td></tr>}
      </tbody></table></div>
      <div className="file-card-foot"><span><ShieldCheck size={15} /> Files are available to signed-in accounts only</span><span>Viewing as <b>{user.id}</b></span></div>
    </div>
    {user.role === 'viewer' && <p className="protection-note"><ShieldCheck size={16} /> Study materials are watermarked in the PDF viewer. Browser protections can discourage copying, but cannot prevent screenshots.</p>}
  </section>;
}

function PdfViewerPage() {
  const { pdfId } = useParams();
  const { user, downloadsEnabled } = useAuth();
  const [pdf, setPdf] = useState(null);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(0);
  const [scale, setScale] = useState(1);
  const [pageWidth, setPageWidth] = useState(800);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pdfTools, setPdfTools] = useState(null);
  const frameRef = useRef(null);
  const canvasRef = useRef(null);
  const pageNodes = useRef(new Map());
  const pageRefCallbacks = useRef(new Map());
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [manualFullscreen, setManualFullscreen] = useState(false);
  const pdfFile = useMemo(() => pdf ? { url: `/api/pdfs/${pdfId}/content`, withCredentials: true } : null, [pdf, pdfId]);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setError('');
    setPdf(null);
    setPages(0);
    setPage(1);
    setScale(1);
    api(`/api/pdfs/${pdfId}`)
      .then((data) => { if (current) setPdf(data); })
      .catch((err) => { if (current) setError(err.message); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [pdfId]);

  useEffect(() => {
    let current = true;
    import('react-pdf').then((tools) => {
      // Keep the worker configuration beside the React PDF components, as required by React PDF.
      tools.pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();
      if (current) setPdfTools(tools);
    }).catch(() => { if (current) setError('The PDF viewer could not be loaded.'); });
    return () => { current = false; };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const fitWidth = () => {
      const style = window.getComputedStyle(canvas);
      const horizontalPadding = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
      const available = canvas.clientWidth - horizontalPadding - 16;
      setPageWidth(Math.max(220, Math.min(1320, Math.floor(available))));
    };
    fitWidth();
    const observer = new ResizeObserver(fitWidth);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const root = canvasRef.current;
    if (!root || !pages || !pageNodes.current.size) return undefined;
    const observer = new IntersectionObserver((entries) => {
      const mostVisible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      const visiblePage = Number(mostVisible?.target.dataset.pdfPage);
      if (visiblePage) setPage((current) => current === visiblePage ? current : visiblePage);
    }, { root, threshold: [0.18, 0.38, 0.58, 0.78] });
    pageNodes.current.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [pages]);

  useEffect(() => {
    const updateFullscreenState = () => {
      setIsFullscreen(document.fullscreenElement === frameRef.current);
      if (!document.fullscreenElement) setManualFullscreen(false);
    };
    document.addEventListener('fullscreenchange', updateFullscreenState);
    return () => document.removeEventListener('fullscreenchange', updateFullscreenState);
  }, []);

  useEffect(() => {
    const block = (event) => { if (event.target.closest?.('.pdf-reader')) event.preventDefault(); };
    const keys = (event) => {
      if (event.ctrlKey || event.metaKey) {
        const key = event.key.toLowerCase();
        if (['c', 'p', 's'].includes(key)) { event.preventDefault(); }
      }
    };
    document.addEventListener('contextmenu', block);
    document.addEventListener('dragstart', block);
    document.addEventListener('selectstart', block);
    document.addEventListener('keydown', keys, true);
    return () => { document.removeEventListener('contextmenu', block); document.removeEventListener('dragstart', block); document.removeEventListener('selectstart', block); document.removeEventListener('keydown', keys, true); };
  }, []);

  const getPageRef = (pageNumber) => {
    if (!pageRefCallbacks.current.has(pageNumber)) {
      pageRefCallbacks.current.set(pageNumber, (node) => {
        if (node) pageNodes.current.set(pageNumber, node);
        else pageNodes.current.delete(pageNumber);
      });
    }
    return pageRefCallbacks.current.get(pageNumber);
  };

  const goToPage = (pageNumber) => {
    const root = canvasRef.current;
    const target = pageNodes.current.get(pageNumber);
    if (!root || !target) return;
    const rootStyle = window.getComputedStyle(root);
    const topPadding = parseFloat(rootStyle.paddingTop) || 0;
    const top = root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top - topPadding;
    root.scrollTo({ top, behavior: 'smooth' });
    setPage(pageNumber);
  };

  const previous = () => goToPage(Math.max(1, page - 1));
  const next = () => goToPage(Math.min(pages, page + 1));
  const setZoom = (delta) => setScale((value) => Math.min(2, Math.max(.6, +(value + delta).toFixed(2))));
  const toggleFullscreen = async () => {
    if (manualFullscreen) { setManualFullscreen(false); return; }
    if (document.fullscreenElement === frameRef.current) { await document.exitFullscreen(); return; }
    try {
      if (frameRef.current?.requestFullscreen) await frameRef.current.requestFullscreen();
      else setManualFullscreen(true);
    } catch {
      setManualFullscreen(true);
    }
  };
  const PdfDocument = pdfTools?.Document;
  const PdfPage = pdfTools?.Page;

  return <section className="pdf-viewer-page">
    <div className="breadcrumb-row"><Link to={pdf ? `/section/${pdf.section}` : '/'} className="back-link"><ArrowLeft size={16} /> Back to {pdf ? `Section ${pdf.section}` : 'study library'}</Link><span className="breadcrumb-separator">/</span><span className="crumb-file">{pdf?.filename || 'PDF viewer'}</span></div>
    <div className="pdf-viewer-heading"><div className="pdf-heading-icon"><FileText size={20} /></div><div className="pdf-heading-copy"><p className="eyebrow">SECURE PDF VIEWER</p><h1>{pdf?.filename || 'Loading document…'}</h1><span>{pdf ? `${formatSize(pdf.size_bytes)} · ${formatDate(pdf.uploaded_at)}` : 'Private study material'}</span></div>{pdf && (downloadsEnabled || user.role === 'admin') && <a className="secondary-button viewer-download" href={`/api/pdfs/${pdfId}/content?download=true`}><ArrowDownToLine size={16} /> Download</a>}</div>
    {error ? <div className="viewer-error"><FileText size={32} /><h2>We couldn’t open this PDF</h2><p>{error}</p><Link to="/">Return to your study hub</Link></div> : <div className={`pdf-reader ${manualFullscreen ? 'viewer-manual-fullscreen' : ''}`} ref={frameRef} onContextMenu={(event) => event.preventDefault()}>
      <div className="pdf-toolbar">
        <div className="pdf-toolbar-title"><FileText size={17} /><span>{pdf?.filename || 'Secure document'}</span></div>
        <div className="pdf-controls" aria-label="PDF controls">
          <button type="button" className="pdf-control-button" onClick={previous} disabled={page <= 1 || !pages} title="Previous page" aria-label="Previous page"><ChevronLeft size={19} /></button>
          <span className="page-indicator"><b>{page}</b><i />{pages || '—'}</span>
          <button type="button" className="pdf-control-button" onClick={next} disabled={!pages || page >= pages} title="Next page" aria-label="Next page"><ChevronRight size={19} /></button>
          <span className="control-divider" />
          <button type="button" className="pdf-control-button" onClick={() => setZoom(-.1)} disabled={scale <= .6} title="Zoom out" aria-label="Zoom out"><ZoomOut size={17} /></button>
          <span className="zoom-label">{Math.round(scale * 100)}%</span>
          <button type="button" className="pdf-control-button" onClick={() => setZoom(.1)} disabled={scale >= 2} title="Zoom in" aria-label="Zoom in"><ZoomIn size={17} /></button>
          <span className="control-divider fullscreen-divider" />
          <button type="button" className="pdf-control-button fullscreen-button" onClick={toggleFullscreen} title={isFullscreen || manualFullscreen ? 'Exit full screen' : 'Enter full screen'} aria-label={isFullscreen || manualFullscreen ? 'Exit full screen' : 'Enter full screen'}>{isFullscreen || manualFullscreen ? <Minimize size={17} /> : <Maximize size={17} />}</button>
        </div>
      </div>
      <div className="pdf-canvas-area" ref={canvasRef}>
        {(loading || !pdfTools || !pdf) ? <div className="pdf-loading"><span className="spinner" />{loading ? 'Opening secure document…' : 'Preparing PDF viewer…'}</div> : PdfDocument ? <PdfDocument file={pdfFile} onLoadSuccess={({ numPages }) => { setPages(numPages); setPage(1); }} onLoadError={(err) => setError(err.message || 'The PDF could not be rendered.')} loading={<div className="pdf-loading"><span className="spinner" /> Loading secure document…</div>} error={<div className="pdf-loading">The document could not be rendered.</div>}>
          {Array.from({ length: pages }, (_, index) => {
            const pageNumber = index + 1;
            return <div className="pdf-page-frame" key={`${pdfId}-${pageNumber}`} data-pdf-page={pageNumber} ref={getPageRef(pageNumber)}>
              <PdfPage pageNumber={pageNumber} width={Math.floor(pageWidth * scale)} renderTextLayer renderAnnotationLayer loading={<div className="page-render-placeholder"><span className="spinner" /> Loading page {pageNumber}…</div>} />
              <div className="page-watermark" aria-hidden="true"><span>{user.id}</span><small>Krunal Study Hub · Private study copy</small></div>
            </div>;
          })}
        </PdfDocument> : <div className="pdf-loading">The PDF viewer could not be initialized.</div>}
      </div>
      <div className="reader-footer"><span><ShieldCheck size={15} /> Secure session · personalized watermark</span><span>Page {page} of {pages || '—'}</span></div>
    </div>}
    <div className="watermark-disclaimer"><ShieldCheck size={16} /><span><b>Personalized study copy.</b> Copying and printing shortcuts are discouraged; browsers cannot prevent screenshots or external capture.</span></div>
  </section>;
}

function AdminPage() {
  const flash = useFlash();
  const { setDownloadsEnabled } = useAuth();
  const [dashboard, setDashboard] = useState(null);
  const [viewers, setViewers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [creating, setCreating] = useState(false);
  const [credentials, setCredentials] = useState(null);
  const [sectionFilter, setSectionFilter] = useState('all');

  const load = useCallback(async () => {
    try {
      const [stats, accounts] = await Promise.all([api('/api/admin/dashboard'), api('/api/admin/viewers')]);
      setDashboard(stats); setViewers(accounts.viewers); setDownloadsEnabled(stats.downloads_enabled);
    } catch (error) { flash(error.message, 'error'); }
    finally { setLoading(false); }
  }, [flash, setDownloadsEnabled]);
  useEffect(() => { load(); }, [load]);

  const createAccount = async (event) => {
    event.preventDefault(); setCreating(true); setCredentials(null);
    try {
      const result = await api('/api/admin/viewers', { method: 'POST', body: JSON.stringify({ user_id: userId, password: password || null }) });
      setCredentials(result); setUserId(''); setPassword(''); flash('Viewer account created. Copy the credentials now.'); load();
    } catch (error) { flash(error.message, 'error'); }
    finally { setCreating(false); }
  };

  const toggleDownloads = async () => {
    const nextValue = !dashboard.downloads_enabled;
    try { await api('/api/admin/settings/downloads', { method: 'PUT', body: JSON.stringify({ enabled: nextValue }) }); setDownloadsEnabled(nextValue); setDashboard((data) => ({ ...data, downloads_enabled: nextValue })); flash(`Viewer downloads ${nextValue ? 'enabled' : 'disabled'}.`); }
    catch (error) { flash(error.message, 'error'); }
  };

  const toggleViewer = async (viewer) => {
    try { await api(`/api/admin/viewers/${encodeURIComponent(viewer.id)}`, { method: 'PATCH', body: JSON.stringify({ active: !viewer.active }) }); setViewers((items) => items.map((item) => item.id === viewer.id ? { ...item, active: !viewer.active } : item)); flash(`${viewer.id} ${viewer.active ? 'deactivated' : 'activated'}.`); }
    catch (error) { flash(error.message, 'error'); }
  };

  const copyCredentials = async () => {
    await navigator.clipboard.writeText(`User ID: ${credentials.user_id}\nPassword: ${credentials.password}`);
    flash('Credentials copied. Save them securely before closing this panel.');
  };

  const visibleRecent = dashboard?.recent_uploads.filter((file) => sectionFilter === 'all' || String(file.section) === sectionFilter) || [];

  if (loading) return <div className="section-loading"><span className="spinner" /> Loading administrator dashboard…</div>;
  return <section className="admin-page">
    <div className="admin-page-head"><div><p className="eyebrow">CONTROL CENTER</p><h1>Admin dashboard</h1><p>Manage your study materials and invited viewer accounts.</p></div><span className="admin-secure-pill"><ShieldCheck size={16} /> Secure administrator session</span></div>
    <div className="stats-grid"><StatCard icon={<FileText size={20} />} label="Total PDFs" value={dashboard?.total_pdfs || 0} detail="Across all five sections" tone="blue" /><StatCard icon={<UserRound size={20} />} label="Viewer accounts" value={dashboard?.total_viewers || 0} detail={`${dashboard?.active_viewers || 0} active accounts`} tone="violet" /><StatCard icon={<Folder size={20} />} label="Study sections" value="05" detail="All sections available" tone="green" /></div>
    <div className="admin-content-grid"><div className="admin-main-column">
      <section className="admin-card"><div className="admin-card-head"><div><p className="eyebrow">LIBRARY ACTIVITY</p><h2>Section overview</h2></div><Link to="/" className="subtle-link">View home <ArrowRight size={15} /></Link></div><div className="section-count-list">{dashboard?.sections.map((item) => <div className="section-count-row" key={item.id}><span className={`section-mini-dot tone-${item.tone}`} /><span className="section-count-name">{item.name}</span><div className="count-track"><span className={`count-fill tone-${item.tone}`} style={{ width: `${dashboard.total_pdfs ? Math.max(4, Math.round(item.count / dashboard.total_pdfs * 100)) : 0}%` }} /></div><b>{item.count}</b><Link to={`/section/${item.id}`} aria-label={`Manage ${item.name}`}><ArrowRight size={15} /></Link></div>)}</div></section>
      <section className="admin-card"><div className="admin-card-head"><div><p className="eyebrow">LATEST ADDED</p><h2>Recent uploads</h2></div><select className="filter-select" value={sectionFilter} onChange={(event) => setSectionFilter(event.target.value)} aria-label="Filter uploads by section"><option value="all">All sections</option>{sectionTones.map((_, index) => <option value={String(index + 1)} key={index}>Section {index + 1}</option>)}</select></div>
        {visibleRecent.length ? <div className="recent-list">{visibleRecent.map((file) => <div className="recent-row" key={file.id}><span className="recent-pdf-icon"><FileText size={17} /></span><div className="recent-file-main"><b>{file.filename}</b><small>Section {file.section} · {formatDate(file.uploaded_at)}</small></div><span className="recent-size">{formatSize(file.size_bytes)}</span><Link to={`/section/${file.section}`} className="recent-manage">Manage <ArrowRight size={14} /></Link></div>)}</div> : <div className="admin-empty"><span className="empty-file-icon"><FileText size={22} /></span><b>No uploads to show</b><p>Upload study PDFs from a section page.</p><div className="admin-empty-links">{sectionTones.map((_, i) => <Link key={i} to={`/section/${i + 1}`}>Section {i + 1} <ArrowRight size={13} /></Link>)}</div></div>}
      </section>
      <section className="admin-card account-manager"><div className="admin-card-head"><div><p className="eyebrow">INVITED ACCESS</p><h2>Viewer accounts <span className="count-tag">{viewers.length}</span></h2></div><span className="admin-secure-pill"><ShieldCheck size={14} /> Admin only</span></div>
        <form className="create-viewer-form" onSubmit={createAccount}><label><span>User ID</span><input required minLength="3" maxLength="40" pattern="[A-Za-z0-9][A-Za-z0-9._-]{2,39}" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="e.g. student01" /></label><label><span>Password <small>(leave blank to generate)</small></span><input minLength="12" maxLength="128" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="12+ characters" /></label><button className="primary-button" type="submit" disabled={creating}>{creating ? <span className="button-spinner" /> : <Plus size={17} />}{creating ? 'Creating…' : 'Create viewer'}</button></form>
        {credentials && <div className="credentials-reveal"><div className="credential-check"><CheckCircle2 size={20} /></div><div className="credential-body"><b>Account created — save these credentials now</b><div><span>User ID</span><code>{credentials.user_id}</code></div><div><span>Password</span><code>{credentials.password}</code></div><small>The password is only shown here and is not stored in plain text.</small></div><button type="button" className="secondary-button copy-button" onClick={copyCredentials}><Copy size={15} /> Copy</button><button type="button" className="icon-button close-credentials" aria-label="Dismiss credentials" onClick={() => setCredentials(null)}><X size={17} /></button></div>}
        {viewers.length ? <div className="table-scroll"><table className="viewer-table"><thead><tr><th>User ID</th><th>Created</th><th>Status</th><th>Access</th></tr></thead><tbody>{viewers.map((viewer) => <tr key={viewer.id}><td><span className="viewer-avatar">{viewer.id.slice(0, 2).toUpperCase()}</span><b>{viewer.id}</b></td><td>{formatDate(viewer.created_at)}</td><td><span className={`status-pill ${viewer.active ? 'status-active' : 'status-paused'}`}><i />{viewer.active ? 'Active' : 'Disabled'}</span></td><td><button type="button" className={`toggle-switch ${viewer.active ? 'toggle-on' : ''}`} aria-label={`${viewer.active ? 'Deactivate' : 'Activate'} ${viewer.id}`} aria-pressed={Boolean(viewer.active)} onClick={() => toggleViewer(viewer)}><span /></button></td></tr>)}</tbody></table></div> : <div className="viewer-empty"><UserRound size={20} /><span>No viewer accounts yet. Create one above to grant access.</span></div>}
      </section>
    </div><aside className="admin-side-column"><section className="settings-card"><div className="settings-icon"><ArrowDownToLine size={20} /></div><p className="eyebrow">VIEWER PERMISSIONS</p><h2>PDF downloads</h2><p>Choose whether viewer accounts can download PDFs from this study hub.</p><div className="setting-row"><span><b>{dashboard?.downloads_enabled ? 'Downloads enabled' : 'Downloads disabled'}</b><small>For viewer accounts</small></span><button type="button" className={`toggle-switch large-toggle ${dashboard?.downloads_enabled ? 'toggle-on' : ''}`} aria-label="Toggle viewer PDF downloads" aria-pressed={Boolean(dashboard?.downloads_enabled)} onClick={toggleDownloads}><span /></button></div><div className={`setting-state ${dashboard?.downloads_enabled ? 'state-enabled' : ''}`}><span />{dashboard?.downloads_enabled ? 'Viewers can download files' : 'Viewers can read PDFs online only'}</div></section>
      <section className="manage-card"><div className="manage-card-icon"><Folder size={18} /></div><div><p className="eyebrow">FILE LIBRARY</p><h2>Manage PDFs</h2><p>Upload, replace or remove a study file inside any section.</p></div><div className="manage-section-links">{dashboard?.sections.map((section) => <Link key={section.id} to={`/section/${section.id}`}><SectionFolder section={section} /><span>{section.name}<small>{section.count} PDFs</small></span><ArrowRight size={15} /></Link>)}</div></section>
      <div className="admin-security-note"><ShieldCheck size={17} /><span><b>Protected by server-side roles</b><small>Viewer accounts cannot upload files or access admin tools.</small></span></div>
    </aside></div>
  </section>;
}

function StatCard({ icon, label, value, detail, tone }) {
  return <div className={`stat-card stat-${tone}`}><span className="stat-icon">{icon}</span><span className="stat-label">{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

export default App;
