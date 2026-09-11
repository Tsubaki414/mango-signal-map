import { GradientWaves } from "./GradientWaves";
import styles from "./HeroV3.module.css";

/* v3 Hero。
 *
 * 波浪层的属性名有个坑：v3 原型写的是 fogdepth / grainintensity / layeropacity
 * （无连字符），而 gradient-waves.js 读的是 fog-depth / grain-intensity /
 * layer-opacity —— 原型里这三个其实**没有生效**，走的是组件默认值。v1 原型用的
 * 是正确的连字符写法。GradientWaves 里按能生效的写法传 v3 声明的数值。 */
export function HeroV3() {
  return (
    <section className={styles.hero} data-screen-label="Hero">
      <GradientWaves />

      <div className={styles.horizon}>
        <svg viewBox="0 0 1440 132" preserveAspectRatio="none" className={styles.bands} aria-hidden="true">
          <g stroke="#F3F1EA" strokeWidth="1.5" vectorEffect="non-scaling-stroke">
            <path d="M0 26H520l10 6h910" opacity=".5" />
            <path d="M0 54H300l10 6h1130" opacity=".33" />
            <path d="M0 82H760l10 6h670" opacity=".22" />
            <path d="M0 110H180l10 6h1250" opacity=".13" />
          </g>
        </svg>
        <div className={styles.tick} />
      </div>

      <div className={styles.copy}>
        <div className={styles.eyebrow}>01 /// KOL LINEUP CONFIGURATOR</div>
        <h1 className={styles.title}>
          <em>Mango Signal Map，</em>
          <span>让重要的人，看见你。</span>
        </h1>
        <p className={styles.lede}>
          说出你想影响的人，<span>我们反向找出</span>
          <span>能把内容送进他们信息流的创作者。</span>
        </p>
        <div className={styles.ctaRow}>
          <a href="#prefs" className={styles.cta}>
            开始选择 <span aria-hidden="true">↓</span>
          </a>
        </div>
        <div className={styles.deliver}>
          <span className={styles.deliverTag}>交付</span>
          <span className={styles.deliverText}>
            一份可执行的传播阵容——每一位都附与目标人物的公开连接记录、合作可行性与预算区间。
          </span>
        </div>
      </div>

      <div className={styles.ringCol}>
        <svg
          viewBox="0 0 620 900"
          preserveAspectRatio="xMidYMax slice"
          className={styles.ring}
          aria-hidden="true"
        >
          <defs>
            <filter id="v3glow" x="-25%" y="-25%" width="150%" height="150%">
              <feGaussianBlur stdDeviation="6" />
            </filter>
          </defs>
          <circle cx="310" cy="600" r="258" fill="none" stroke="#F3F1EA" strokeWidth="6" opacity=".4" filter="url(#v3glow)" />
          <circle
            cx="310"
            cy="600"
            r="258"
            fill="none"
            stroke="#F3F1EA"
            strokeWidth="1.5"
            strokeDasharray="2400"
            strokeDashoffset="2400"
            vectorEffect="non-scaling-stroke"
            className={styles.ringDraw}
          />
        </svg>
      </div>
    </section>
  );
}
