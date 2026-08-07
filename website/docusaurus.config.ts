import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Digit',
  tagline: 'The self-improving AI agent',
  favicon: 'img/favicon.ico',

  // Свой хост документации форка. У апстрима сайт живёт на Vercel, а /docs —
  // подпуть маркетингового лендинга, отсюда старый baseUrl '/docs/'. Здесь под
  // документацию отдан весь домен (GitHub Pages репозитория digitable-lol/digit
  // + CNAME в static/), поэтому baseUrl — корень: адрес страницы больше не
  // повторяет слово docs дважды.
  //
  // Совместимость: ~500 внутренних ссылок были записаны как /docs/<путь> —
  // при baseUrl '/docs/' Docusaurus отдавал их как есть, при '/' они бы
  // упирались в 404. Генератор страниц навыков исправлен, а плагин редиректов
  // ниже дополнительно ловит любой оставшийся /docs/* (и старые закладки).
  url: 'https://docs.digitable.life',
  baseUrl: '/',

  organizationName: 'digitable-lol',
  projectName: 'digit',

  onBrokenLinks: 'warn',

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh-Hans'],
    localeConfigs: {
      en: {
        label: 'English',
      },
      'zh-Hans': {
        label: '简体中文',
        htmlLang: 'zh-Hans',
      },
    },
  },

  themes: [
    '@docusaurus/theme-mermaid',
    [
      require.resolve('@easyops-cn/docusaurus-search-local'),
      /** @type {import("@easyops-cn/docusaurus-search-local").PluginOptions} */
      ({
        hashed: true,
        language: ['en', 'zh'],
        indexBlog: false,
        docsRouteBasePath: '/',
        // Disabled: appends ?_highlight=... to URLs (before the #anchor),
        // which makes copy/pasted doc links ugly. Ctrl+F on the page is fine.
        highlightSearchTermsOnTargetPage: false,
        // Exclude the auto-generated per-skill catalog pages from search.
        // There are hundreds of them and they dominate results for generic
        // terms, drowning out the real user-guide / reference docs.
        // The two human-written catalog indexes (reference/skills-catalog,
        // reference/optional-skills-catalog) remain indexed.
        //
        // Note: ignoreFiles matches `route` (baseUrl stripped, no leading
        // slash). With baseUrl '/', `/user-guide/skills/bundled/x`
        // becomes 'user-guide/skills/bundled/x'.
        ignoreFiles: [
          /^user-guide\/skills\/bundled\//,
          /^user-guide\/skills\/optional\//,
        ],
        // Exact-or-prefix matching only (default is edit distance 1).
        // With fuzzy distance 1, "keet" matched "meetings"/"keep" (one
        // edit away after stemming), and multi-word typo queries against
        // our ~14 MB index could stall the single-threaded search worker
        // for 25s+, backing up every subsequent keystroke's search until
        // the bar appeared dead. Distance 0 keeps "word or its extension"
        // semantics (keet -> keet*) and removes the pathological scans.
        fuzzyMatchingDistance: 0,
      }),
    ],
  ],

  plugins: [
    [
      '@docusaurus/plugin-client-redirects',
      {
        // Страховка на переезд с baseUrl '/docs/' на '/'. Внутренние ссылки
        // вида /docs/<путь> (генератор страниц навыков, каталоги, отдельные
        // ручные ссылки) и любые внешние закладки на старый путь иначе стали
        // бы 404. Генерируем зеркальный редирект для каждой существующей
        // страницы, а не список вручную: страниц ~900 и они меняются.
        createRedirects(existingPath: string) {
          return [`/docs${existingPath}`];
        },

        // Static-host redirects for renamed doc pages (GitHub Pages can't
        // do server-side redirects). Paths are relative to baseUrl (/).
        redirects: [
          {
            // Renamed in #44470 (Automation Blueprints terminology rebrand)
            from: '/guides/automation-templates',
            to: '/guides/automation-blueprints',
          },
          {
            // Moved when the Plugins subcategory was created under
            // Developer Guide > Extending (docs restructure, July 2026)
            from: '/guides/build-a-digit-plugin',
            to: '/developer-guide/plugins',
          },
          {
            // Users guess these short paths from abbreviated links and hit
            // raw 404s (consumer-onboarding audit finding #1, Aug 2026).
            from: '/quickstart',
            to: '/getting-started/quickstart',
          },
          {
            // На эти два адреса ссылаются справка CLI и навык digit, но
            // страниц по ним никогда не было: `link: generated-index` в
            // _category_.json действует только для автогенерируемого сайдбара,
            // а sidebars.ts здесь написан руками. Ссылки были битыми и на
            // сайте апстрима — уводим на первую страницу раздела.
            from: '/developer-guide',
            to: '/developer-guide/architecture',
          },
          {
            from: '/user-guide',
            to: '/user-guide/cli',
          },
          {
            from: '/installation',
            to: '/getting-started/installation',
          },
        ],
      },
    ],
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',  // Docs at the root of the site
          sidebarPath: './sidebars.ts',
          // «Edit this page» должна вести в репозиторий, где страница лежит.
          // Апстримовый путь открывал бы редактор чужого файла, до которого у
          // читателя Digit нет прав и в котором нет наших правок.
          editUrl: 'https://github.com/digitable-lol/digit/edit/main/website/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    image: 'img/digit-banner.png',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    docs: {
      sidebar: {
        hideable: true,
        autoCollapseCategories: true,
      },
    },
    navbar: {
      title: 'Digit',
      logo: {
        alt: 'Digit',
        src: 'img/logo.png',
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'docs',
          position: 'left',
          label: 'Docs',
        },
        {
          to: '/skills',
          label: 'Skills',
          position: 'left',
        },
        {
          // Было — лендинг апстрима с установщиком Hermes Agent. Ставить по
          // нему Digit нельзя: скачается другой агент. Ведём на нашу же
          // страницу установки.
          to: '/getting-started/installation',
          label: 'Install',
          position: 'left',
        },
        {
          type: 'localeDropdown',
          position: 'right',
        },
        {
          href: 'https://digitable.life',
          label: 'Home',
          position: 'right',
        },
        {
          href: 'https://github.com/digitable-lol/digit',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            { label: 'Getting Started', to: '/getting-started/quickstart' },
            { label: 'User Guide', to: '/user-guide/cli' },
            { label: 'Developer Guide', to: '/developer-guide/architecture' },
            { label: 'Reference', to: '/reference/cli-commands' },
          ],
        },
        {
          title: 'Community',
          items: [
            // Баг-репорты по Digit заводят у нас: у апстрима нашего кода нет.
            { label: 'GitHub Issues', href: 'https://github.com/digitable-lol/digit/issues' },
            { label: 'Digitable', href: 'https://digitable.life' },
            { label: 'Skills Hub', href: 'https://agentskills.io' },
            // Сообщество апстрима — подписано так, чтобы никто не принял его
            // за канал поддержки Digit.
            { label: 'Nous Research Discord', href: 'https://discord.gg/NousResearch' },
          ],
        },
        {
          title: 'More',
          items: [
            { label: 'Install', to: '/getting-started/installation' },
            { label: 'GitHub', href: 'https://github.com/digitable-lol/digit' },
            // Происхождение форка: условие MIT-лицензии Hermes Agent, ссылку
            // не убирать (см. NOTICE в корне репозитория).
            { label: 'Hermes Agent (upstream)', href: 'https://github.com/NousResearch/hermes-agent' },
            { label: 'Nous Research', href: 'https://nousresearch.com' },
          ],
        },
      ],
      copyright: `Built by <a href="https://digitable.life">Digitable</a> · Based on <a href="https://github.com/NousResearch/hermes-agent">Hermes Agent</a> by Nous Research · MIT License · ${new Date().getFullYear()}`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'yaml', 'json', 'python', 'toml'],
    },
    mermaid: {
      theme: {light: 'neutral', dark: 'dark'},
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
